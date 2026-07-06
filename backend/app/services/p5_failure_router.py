"""P5 失败返工闭环（R12-3-C6）。

实现验证失败后的真实返工路由（不伪造、不静默）：

  失败类型                    路由
  ─────────────────────────── ─────────────────────────────
  验证命令执行失败/超时    → P5 retry（有界 ≤N 次）
  output_code 缺失         → blocked / P4 rework
  patch 缺失               → blocked / P4 rework
  Evidence 缺失            → blocked + Evidence Gap
  构建失败                  → P4 rework / PlanDelta
  测试失败                  → P4 rework / PlanDelta
  静态检查失败              → 记录 + PlanDelta（不强制 rework）
  用户 request_changes     → PlanDelta + P4 rework
  Evidence Gap             → Gate 请求用户提供或接受风险
  L4/L5 高风险命令          → Gate 拦截（拒绝自动放行）

约束：
  - P5 不直接修改 output_code（代码问题回 P4 rework）
  - 旧验证结果保留 superseded 关系
  - 重试有界（≤max_retries），超出 → Gate 升级
  - Auto 模式也必须 Gate 高风险升级

本环节新增失败路由器 + 证据缺口 Gate + PlanDelta 路由 + 真实项目实测。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.core.database import get_session
from app.services.plan_delta_service import PlanDeltaService

logger = logging.getLogger(__name__)


# ── DTOs ──────────────────────────────────────────────────────────────────

@dataclass
class P5FailureRoute:
    """P5 失败路由决策。"""
    failure_type: str               # retry / p4_rework / evidence_gap / gate / plan_delta
    action: str                     # 具体行动描述
    retry_allowed: bool = False     # 是否允许 P5 retry
    retry_count: int = 0            # 当前重试次数
    max_retries: int = 2            # 最大重试次数（有界）
    p4_rework_required: bool = False  # 是否需要回 P4 rework
    gate_required: bool = False     # 是否需要创建 Gate
    gate_reason: str = ""           # Gate 原因
    plan_delta_type: Optional[str] = None  # PlanDelta 类型（如适用）
    plan_delta_reason: str = ""     # PlanDelta 原因
    blocked: bool = True            # 是否阻止 P5 completed
    superseded_old: bool = False    # 是否替代旧验证结果


# ── 失败类型 ───────────────────────────────────────────────────────────────

class P5FailureType:
    """P5 验证失败分类。"""
    COMMAND_TIMEOUT = "command_timeout"
    COMMAND_FAILED = "command_failed"
    OUTPUT_CODE_MISSING = "output_code_missing"
    PATCH_MISSING = "patch_missing"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_INVALID = "evidence_invalid"
    BUILD_FAILED = "build_failed"
    TEST_FAILED = "test_failed"
    STATIC_CHECK_FAILED = "static_check_failed"
    L4_L5_RISK = "l4_l5_risk"
    NEEDS_USER_INPUT = "needs_user_input"


# ── 路由器 ─────────────────────────────────────────────────────────────────

class P5FailureRouter:
    """P5 失败路由决策器。R12-3-C6 新建。"""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries

    def route(self, failure_type: str, context: dict | None = None) -> P5FailureRoute:
        """根据失败类型路由到正确的返工路径。"""
        ctx = context or {}
        retry_count = ctx.get("retry_count", 0)

        # ── 有界重试（验证命令自身失败/超时）──
        if failure_type == P5FailureType.COMMAND_TIMEOUT:
            return self._retry_route(retry_count, "命令执行超时")
        if failure_type == P5FailureType.COMMAND_FAILED:
            return self._retry_route(retry_count, "命令执行失败")

        # ── output_code / patch 缺失 → P4 rework ──
        if failure_type == P5FailureType.OUTPUT_CODE_MISSING:
            return P5FailureRoute(
                failure_type=failure_type,
                action="P4 output_code 缺失 → blocked + P4 rework",
                p4_rework_required=True, blocked=True,
                plan_delta_type="blocking_adjustment",
                plan_delta_reason="P4 未产出 output_code，需重新执行 P4",
            )
        if failure_type == P5FailureType.PATCH_MISSING:
            return P5FailureRoute(
                failure_type=failure_type,
                action="P4 patch 缺失 → blocked + P4 rework",
                p4_rework_required=True, blocked=True,
                plan_delta_type="blocking_adjustment",
                plan_delta_reason="P4 未产出 patch/diff，需重新执行 P4",
            )

        # ── Evidence 缺失 / 无效 ──
        if failure_type == P5FailureType.EVIDENCE_MISSING:
            return P5FailureRoute(
                failure_type=failure_type,
                action="Evidence 缺失 → blocked + Evidence Gap + Gate（用户确认）",
                gate_required=True, blocked=True,
                gate_reason="P4 Evidence 缺失，无法验证正确性。请补充 Evidence 或接受风险。",
                plan_delta_type="validation_change",
                plan_delta_reason="Evidence 缺失导致 P5 无法完成验证",
            )
        if failure_type == P5FailureType.EVIDENCE_INVALID:
            return P5FailureRoute(
                failure_type=failure_type,
                action="Evidence basis 无效（sha256 不匹配）→ blocked + Gate",
                gate_required=True, blocked=True,
                gate_reason="Evidence sha256 与实际文件不一致（D-101 反伪造）",
                plan_delta_type="validation_change",
                plan_delta_reason="Evidence basis 无效（可能被篡改或 LLM 自报）",
            )

        # ── 构建/测试失败 → P4 rework / PlanDelta ──
        if failure_type == P5FailureType.BUILD_FAILED:
            return P5FailureRoute(
                failure_type=failure_type,
                action="构建失败 → PlanDelta + P4 rework（代码需修复）",
                p4_rework_required=True, blocked=True,
                plan_delta_type="blocking_adjustment",
                plan_delta_reason="构建命令执行失败，代码需修复后重新 P4",
            )
        if failure_type == P5FailureType.TEST_FAILED:
            return P5FailureRoute(
                failure_type=failure_type,
                action="测试失败 → PlanDelta + P4 rework（代码需修复）",
                p4_rework_required=True, blocked=True,
                plan_delta_type="blocking_adjustment",
                plan_delta_reason="测试命令执行失败，代码需修复后重新 P4",
            )
        if failure_type == P5FailureType.STATIC_CHECK_FAILED:
            return P5FailureRoute(
                failure_type=failure_type,
                action="静态检查失败 → PlanDelta（记录但非强制 rework）",
                p4_rework_required=False, blocked=False,  # 静态检查不阻塞 P5
                plan_delta_type="validation_change",
                plan_delta_reason="静态检查发现问题（待后续修复）",
            )

        # ── L4/L5 高风险 → Gate 拦截 ──
        if failure_type == P5FailureType.L4_L5_RISK:
            return P5FailureRoute(
                failure_type=failure_type,
                action="L4/L5 高风险命令 → Gate 拦截（禁止自动放行）",
                gate_required=True, blocked=True,
                gate_reason="命令被分类为 L4/L5 高风险，须用户确认后放行",
            )

        # ── needs_user_input → Gate ──
        if failure_type == P5FailureType.NEEDS_USER_INPUT:
            return P5FailureRoute(
                failure_type=failure_type,
                action="命令不可识别 → Gate 请求用户提供",
                gate_required=True, blocked=True,
                gate_reason="命令不可自动识别，需用户提供构建/测试/静态检查命令",
            )

        # 默认 → blocked + Gate
        return P5FailureRoute(
            failure_type=failure_type,
            action=f"未知失败类型 {failure_type} → blocked + Gate",
            gate_required=True, blocked=True,
            gate_reason=f"未知失败类型：{failure_type}",
        )

    def _retry_route(self, retry_count: int, reason: str) -> P5FailureRoute:
        """有界重试路由。"""
        if retry_count < self.max_retries:
            return P5FailureRoute(
                failure_type="retry",
                action=f"P5 retry ({retry_count + 1}/{self.max_retries})：{reason}",
                retry_allowed=True,
                retry_count=retry_count + 1,
                max_retries=self.max_retries,
                blocked=True,
            )
        # 重试耗尽 → Gate 升级
        return P5FailureRoute(
            failure_type="retry_exhausted",
            action=f"重试 {self.max_retries} 次仍失败 → Gate 升级",
            retry_allowed=False,
            retry_count=retry_count,
            gate_required=True, blocked=True,
            gate_reason=f"P5 验证失败经 {self.max_retries} 次重试仍未通过：{reason}",
        )

    def should_create_gate(self, route: P5FailureRoute) -> bool:
        """是否需要创建 Gate。"""
        return route.gate_required

    def should_p4_rework(self, route: P5FailureRoute) -> bool:
        """是否需要回 P4 rework。"""
        return route.p4_rework_required


# ── PlanDelta 集成 ─────────────────────────────────────────────────────────

def create_plan_delta_for_failure(route: P5FailureRoute, project_id: str,
                                   stage: str = "p5", run_id: str = "",
                                   auditor=None) -> Optional[str]:
    """为失败路由创建 PlanDelta 记录（如适用）。"""
    if route.plan_delta_type is None:
        return None
    try:
        db = get_session()
        try:
            svc = PlanDeltaService(db, auditor=auditor)
            delta = svc.create_delta(
                project_id=project_id,
                delta_type=route.plan_delta_type,
                reason=route.plan_delta_reason,
                change_summary=route.action,
                risk_impact=None,
                run_id=run_id,
                stage=stage,
                created_by="p5_failure_router",
            )
            return getattr(delta, "plan_delta_id", None)
        finally:
            db.close()
    except Exception as e:
        logger.warning("P5 failure router: PlanDelta create failed: %s", e, exc_info=True)
        return None


__all__ = [
    "P5FailureRouter", "P5FailureRoute", "P5FailureType",
    "create_plan_delta_for_failure",
]
