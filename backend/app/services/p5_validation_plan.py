"""P5 validation plan 与全量验证 Evidence 状态机（R12-3-C2）。

建立 P5 验证计划数据结构和 Evidence 状态机，将全量真实构建/运行/测试/静态检查
写入最小验证要求（D-105①）。

核心数据结构：
  - P5ValidationPlan：表达完整的 10 槽位验证计划
  - P5ValidationSlot：单个验证槽位（硬必需 / 有条件必需 / 增强项）
  - P5SlotStatus：槽位状态（含 evidence_gap / needs_user_input 一等状态）
  - P5EvidenceStatus：Evidence 验证状态（扩展 AET 10 态 + P5 特有态）
  - transition_slot_status()：状态转换校验器（防非法跳转）
  - can_mark_completed()：P5 能否标记 completed 的判定（D-105① 硬约束）

诚实原则（V10 教训 / D-066 / D-101 / D-097）：
  - 命令不可识别 → evidence_gap + needs_user_input（不伪造通过）
  - 命令执行失败 → validation_failed（不等于 completed）
  - Auto 模式也不可绕过真实构建/运行/测试/静态检查
  - P5 completed 必须所有硬必需槽位 validated + 有条件必需槽位 validated 或
    经 Gate 接受风险的 evidence_gap

本环节只新增数据结构和状态机 + 单测。**不执行真实命令**（C5），**不注册 P5 handler**（C3）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Slot type ─────────────────────────────────────────────────────────────

class P5SlotType(str, Enum):
    """验证槽位类型（R12-2 §6.2 八槽位分层扩展为 10 槽位）。"""
    HARD_REQUIRED = "hard_required"       # 缺则 P5 不得 completed
    CONDITIONAL = "conditional"           # 能识别则执行，不能识别则 evidence_gap
    ENHANCED = "enhanced"                 # R12 最小链路可不含，R13+ 扩展


# ── Slot status ───────────────────────────────────────────────────────────

class P5SlotStatus(str, Enum):
    """P5 验证槽位状态（evidence_gap / needs_user_input 为一等状态）。

    扩展自 AET Evidence 10 态（candidate / submitted / under_validation /
    validated / insufficient / rejected / superseded / archived / invalid +
    validation_pending），新增 P5 特有的：
      - evidence_gap：验证动作发现证据缺失（文件/refs/Evidence 不存在）
      - needs_user_input：命令不可识别、工具不可用、环境不可用，需用户提供
    """
    # 初始 / 进行中
    PENDING = "pending"
    VALIDATION_PENDING = "validation_pending"
    IN_PROGRESS = "in_progress"
    # 终态 - 通过
    VALIDATED = "validated"
    # 终态 - 失败 / 缺失
    VALIDATION_FAILED = "validation_failed"
    EVIDENCE_GAP = "evidence_gap"
    NEEDS_USER_INPUT = "needs_user_input"
    # 终态 - 其他
    SUPERSEDED = "superseded"             # 被后续验证替代
    NOT_APPLICABLE = "not_applicable"     # 明确不适用（需 Audit 记录理由）


# ── Slot ID constants（10 槽位）────────────────────────────────────────────

class P5SlotId(str, Enum):
    """P5 验证槽位 ID（R12-2 §6.2 + D-105① 全量真实命令）。"""
    # 硬必需（缺则 P5 不得 completed）
    OUTPUT_CODE_EXISTS = "output_code_exists"           # 1. output_code 存在且非空
    PATCHES_EXIST = "patches_exist"                     # 2. patches 存在且与 output_code 对应
    P4_EVIDENCE_REAL = "p4_evidence_real"              # 3. P4 Evidence 真实性
    P4_SUMMARY_READABLE = "p4_summary_readable"        # 4. P4 summary 可读
    P4_P5_GATE_APPROVED = "p4_p5_gate_approved"        # 5. P4→P5 Gate approved
    # 有条件必需（能识别则执行，不能识别则 evidence_gap）
    BUILD_VERIFIED = "build_verified"                  # 6. 真实构建
    RUN_VERIFIED = "run_verified"                      # 7. 真实运行/启动
    TESTS_PASS = "tests_pass"                          # 8. 真实测试
    STATIC_CHECK = "static_check"                      # 9. 真实静态/语法检查
    # 增强项（R12 最小链路可不含）
    SOURCE_UNMODIFIED = "source_unmodified"            # 10. source 未被修改（sha256）


# ── 十槽位 ↔ 文档 6/8 证据类别 词表映射（GAP-P5-7，R17.5-P5-R3）─────────────
# 背景：运行时以本文件的【十槽位 P5SlotId】为权威（can_mark_completed 唯一读取的门禁枚举）；
# 文档 08-测试与验收/02（§3 六证据槽位）与 04（§3 八证据槽位）用的是【面向 agent 的证据类别
# 词表】。用户裁决（2026-07-27 裁决4）：以【面向 agent 的证据类别】为准，运行时十槽位与文档
# 6/8 类别做【映射对齐】即可——不新增刚性枚举、不加门禁约束，仅作词表对照说明。
# P5 stage skill（P-migration-verification）§验证维度已把这些证据类别写为"指引非必填槽位"，
# 新维度证据【桥接进现有十槽】或【诚实登记 evidence_gap】，不另立枚举。
#
#   运行时十槽位（本文件，门禁权威）      文档 02 §3（6 类）        文档 04 §3（8 类）
#   ─────────────────────────────────  ──────────────────────  ────────────────────────────
#   output_code_exists (hard)          （P4 产物前置，非验证证据类别；桥接进 verification_trace 溯源）
#   patches_exist      (hard)          差异登记                 verification_trace（+LLM structure_mapping 补语义对应）
#   p4_evidence_real   (hard)          （反伪造 sha256 前置）    verification_audit / build·test evidence 之 basis
#   p4_summary_readable(hard)          （P4 摘要可解析前置）      verification_trace
#   p4_p5_gate_approved(hard)          （P4→P5 Gate 前置）       verification_audit
#   build_verified     (cond)          构建证据                 build_evidence
#   run_verified       (cond)          启动运行证据              runtime_evidence
#   tests_pass         (cond)          测试证据                 test_evidence
#   static_check       (cond)          （静态检查，并入构建/测试） build_evidence（静态子项）
#   source_unmodified  (enh)           （源只读正向证据，D-099）  verification_audit（源未改）
#
# 文档 6/8 类别中运行时十槽位【未直接立槽】的（回归对比/behavior_evidence/security_redaction_evidence
# /benchmark 等）→ 由 capability-first 的 dimension_capabilities 分区（非门禁）+ P5 skill 维度指引
# 承载，环境具备时桥接进现有槽/证据、缺失则诚实 evidence_gap，均不新增刚性门禁槽位。


# ── Slot → Type 映射 ──────────────────────────────────────────────────────

SLOT_TYPE_MAP: dict[str, P5SlotType] = {
    # 硬必需
    P5SlotId.OUTPUT_CODE_EXISTS: P5SlotType.HARD_REQUIRED,
    P5SlotId.PATCHES_EXIST: P5SlotType.HARD_REQUIRED,
    P5SlotId.P4_EVIDENCE_REAL: P5SlotType.HARD_REQUIRED,
    P5SlotId.P4_SUMMARY_READABLE: P5SlotType.HARD_REQUIRED,
    P5SlotId.P4_P5_GATE_APPROVED: P5SlotType.HARD_REQUIRED,
    # 有条件必需
    P5SlotId.BUILD_VERIFIED: P5SlotType.CONDITIONAL,
    P5SlotId.RUN_VERIFIED: P5SlotType.CONDITIONAL,
    P5SlotId.TESTS_PASS: P5SlotType.CONDITIONAL,
    P5SlotId.STATIC_CHECK: P5SlotType.CONDITIONAL,
    # 增强项
    P5SlotId.SOURCE_UNMODIFIED: P5SlotType.ENHANCED,
}

# 硬必需槽位 ID 集合（快速查询）
HARD_REQUIRED_SLOTS = {sid for sid, stype in SLOT_TYPE_MAP.items() if stype == P5SlotType.HARD_REQUIRED}

# 有条件必需槽位 ID 集合
CONDITIONAL_SLOTS = {sid for sid, stype in SLOT_TYPE_MAP.items() if stype == P5SlotType.CONDITIONAL}


# ── DTOs ──────────────────────────────────────────────────────────────────

@dataclass
class P5ValidationSlot:
    """单个 P5 验证槽位。"""
    slot_id: str
    slot_type: str  # P5SlotType value
    status: str = P5SlotStatus.PENDING
    # 验证内容描述
    description: str = ""
    # 证据引用（evidence_id 列表）
    evidence_refs: list[str] = field(default_factory=list)
    # 命令信息（有条件必需槽位）
    command: Optional[str] = None          # 识别/配置的命令
    command_available: bool = False        # 命令是否可识别/可用
    # 执行结果（C5 起填充）
    exit_code: Optional[int] = None
    stdout_summary: Optional[str] = None
    stderr_summary: Optional[str] = None
    duration_ms: Optional[int] = None
    # 失败 / 缺失详情
    failure_reason: Optional[str] = None
    evidence_gap_reason: Optional[str] = None
    needs_user_input_prompt: Optional[str] = None  # 需要用户提供什么
    # Gate 接受风险（evidence_gap 经用户 Gate 接受后可视为"有条件下通过"）
    risk_accepted_via_gate: bool = False
    gate_id: Optional[str] = None
    # 元数据
    superseded_by: Optional[str] = None    # 被哪个槽位替代
    not_applicable_reason: Optional[str] = None
    # 验证详情（C4 P5VerificationService 填充）
    details: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)


@dataclass
class P5ValidationPlan:
    """P5 完整验证计划（10 槽位）。"""
    project_id: str
    run_id: str
    slots: list[P5ValidationSlot] = field(default_factory=list)
    # 全局状态
    created_at: str = ""
    updated_at: str = ""
    # 诚实标记
    all_hard_required_validated: bool = False
    all_conditional_validated_or_accepted: bool = False
    can_be_completed: bool = False
    blocked_reason: Optional[str] = None

    def get_slot(self, slot_id: str) -> Optional[P5ValidationSlot]:
        for s in self.slots:
            if s.slot_id == slot_id:
                return s
        return None

    def get_slots_by_type(self, slot_type: str) -> list[P5ValidationSlot]:
        return [s for s in self.slots if s.slot_type == slot_type]


# ── 状态转换规则 ──────────────────────────────────────────────────────────

# 合法转换：from_status → {to_status, ...}
VALID_TRANSITIONS: dict[str, set[str]] = {
    P5SlotStatus.PENDING: {
        P5SlotStatus.VALIDATION_PENDING, P5SlotStatus.IN_PROGRESS,
        P5SlotStatus.EVIDENCE_GAP, P5SlotStatus.NEEDS_USER_INPUT,
        P5SlotStatus.NOT_APPLICABLE,
    },
    P5SlotStatus.VALIDATION_PENDING: {
        P5SlotStatus.IN_PROGRESS, P5SlotStatus.EVIDENCE_GAP,
        P5SlotStatus.NEEDS_USER_INPUT, P5SlotStatus.VALIDATION_FAILED,
    },
    P5SlotStatus.IN_PROGRESS: {
        P5SlotStatus.VALIDATED, P5SlotStatus.VALIDATION_FAILED,
        P5SlotStatus.EVIDENCE_GAP, P5SlotStatus.NEEDS_USER_INPUT,
        P5SlotStatus.NOT_APPLICABLE,  # R12-16: 命令-less 槽位（如库类 run）
    },
    # 终态不可再转（除 superseded 外）
    P5SlotStatus.VALIDATED: {P5SlotStatus.SUPERSEDED},
    P5SlotStatus.VALIDATION_FAILED: {P5SlotStatus.SUPERSEDED},
    P5SlotStatus.EVIDENCE_GAP: {P5SlotStatus.SUPERSEDED},  # 可被后续验证替代
    P5SlotStatus.NEEDS_USER_INPUT: {P5SlotStatus.SUPERSEDED},
    P5SlotStatus.SUPERSEDED: set(),
    P5SlotStatus.NOT_APPLICABLE: set(),
}


def transition_slot_status(slot: P5ValidationSlot, new_status: str) -> P5ValidationSlot:
    """校验并执行槽位状态转换。非法转换 → ValueError（防伪造）。"""
    current = slot.status
    allowed = VALID_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValueError(
            f"非法状态转换：{current} → {new_status}（允许：{allowed or '无（终态）'}）"
        )
    slot.status = new_status
    return slot


# ── P5 completed 判定（D-105① 硬约束）─────────────────────────────────────

def can_mark_completed(plan: P5ValidationPlan) -> tuple[bool, str]:
    """P5 能否标记 completed 的判定。

    硬约束（D-105①）：
      1. 所有硬必需槽位必须 validated
      2. 有条件必需槽位必须 validated 或（evidence_gap + risk_accepted_via_gate）
      3. 不存在未解决的 needs_user_input
      4. Auto 模式也不可绕过

    返回：(can_complete: bool, reason: str)
    """
    # 1. 硬必需
    for sid in HARD_REQUIRED_SLOTS:
        slot = plan.get_slot(sid)
        if slot is None:
            return False, f"硬必需槽位 {sid} 未创建"
        if slot.status != P5SlotStatus.VALIDATED:
            return False, f"硬必需槽位 {sid} 未通过（status={slot.status}）"

    # 2. 有条件必需
    for sid in CONDITIONAL_SLOTS:
        slot = plan.get_slot(sid)
        if slot is None:
            return False, f"有条件必需槽位 {sid} 未创建"
        if slot.status == P5SlotStatus.VALIDATED:
            continue
        if slot.status == P5SlotStatus.EVIDENCE_GAP and slot.risk_accepted_via_gate:
            continue  # 经 Gate 接受风险
        if slot.status == P5SlotStatus.NOT_APPLICABLE:
            continue  # 明确不适用
        return False, (
            f"有条件必需槽位 {sid} 未通过（status={slot.status}，"
            f"risk_accepted={slot.risk_accepted_via_gate}）"
        )

    # 3. 未解决的 needs_user_input
    for slot in plan.slots:
        if slot.status == P5SlotStatus.NEEDS_USER_INPUT:
            return False, f"槽位 {slot.slot_id} 需要用户输入（{slot.needs_user_input_prompt}）"

    return True, "所有硬必需 + 有条件必需槽位已验证或经 Gate 接受风险"


# ── Plan 工厂 ─────────────────────────────────────────────────────────────

def create_p5_validation_plan(project_id: str, run_id: str) -> P5ValidationPlan:
    """创建完整的 10 槽位 P5 validation plan（初始状态全 PENDING）。"""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    SLOT_DESCRIPTIONS = {
        P5SlotId.OUTPUT_CODE_EXISTS: "P4 output_code 文件存在且非空",
        P5SlotId.PATCHES_EXIST: "P4 patches/diff 存在且与 output_code 对应",
        P5SlotId.P4_EVIDENCE_REAL: "P4 Evidence basis 真实（非 LLM 自报）",
        P5SlotId.P4_SUMMARY_READABLE: "artifacts/p4/p4_execution_summary.json 可解析",
        P5SlotId.P4_P5_GATE_APPROVED: "P4→P5 Gate 状态为 approved",
        P5SlotId.BUILD_VERIFIED: "经 ExecutionProvider 真实执行构建命令",
        P5SlotId.RUN_VERIFIED: "经 ExecutionProvider 真实执行运行/启动 + 健康检查",
        P5SlotId.TESTS_PASS: "经 ExecutionProvider 真实执行测试命令",
        P5SlotId.STATIC_CHECK: "经 ExecutionProvider 真实执行静态/语法检查",
        P5SlotId.SOURCE_UNMODIFIED: "source/ byte 级未变（sha256 对比）",
    }

    slots = []
    for sid in P5SlotId:
        slots.append(P5ValidationSlot(
            slot_id=sid.value,
            slot_type=SLOT_TYPE_MAP[sid].value,
            status=P5SlotStatus.PENDING,
            description=SLOT_DESCRIPTIONS.get(sid, ""),
        ))

    return P5ValidationPlan(
        project_id=project_id,
        run_id=run_id,
        slots=slots,
        created_at=now,
        updated_at=now,
    )


# ── 序列化 ────────────────────────────────────────────────────────────────

def slot_to_dict(slot: P5ValidationSlot) -> dict:
    """R19-1（修 B4，最致命的一处）：旧实现**不序列化 `details`**。

    `stdout_summary` / `stderr_summary` 两个字段虽然存在，却从来没有被任何写入方赋值
    （verify 侧把输出放进 `slot.details["stdout_tail"]`）⇒ 真实的 NU1101 / CS 诊断文本
    根本走不到 `artifacts/p5_validation_report.json`，也就到不了前端。
    本轮补 `details` / `issues` / `artifacts` 序列化，并把 `stdout_summary`/`stderr_summary`
    真实回填为 details 中的尾巴（不再是永远为 null 的死字段）。
    """
    details = slot.details or {}
    return {
        "slot_id": slot.slot_id,
        "slot_type": slot.slot_type,
        "status": slot.status,
        "description": slot.description,
        "evidence_refs": slot.evidence_refs,
        "command": slot.command,
        "command_available": slot.command_available,
        "exit_code": slot.exit_code,
        # 真实回填（旧实现恒 None）：来源 = 命令真实 stdout/stderr 尾巴
        "stdout_summary": slot.stdout_summary or details.get("stdout_tail") or None,
        "stderr_summary": slot.stderr_summary or details.get("stderr_tail") or None,
        "duration_ms": slot.duration_ms if slot.duration_ms is not None
                       else details.get("elapsed_ms"),
        "failure_reason": slot.failure_reason,
        "evidence_gap_reason": slot.evidence_gap_reason,
        "needs_user_input_prompt": slot.needs_user_input_prompt,
        "risk_accepted_via_gate": slot.risk_accepted_via_gate,
        "gate_id": slot.gate_id,
        "superseded_by": slot.superseded_by,
        "not_applicable_reason": slot.not_applicable_reason,
        # R19-1（修 B4）：验证详情必须出到报告——含 diagnostics / diagnostics_summary /
        # stages / execution（镜像引用+digest / 挂载清单 / 硬化档自述）/ log_ref。
        "details": details,
        "issues": slot.issues,
        "artifacts": slot.artifacts,
    }


def plan_to_dict(plan: P5ValidationPlan) -> dict:
    can_complete, reason = can_mark_completed(plan)
    return {
        "project_id": plan.project_id,
        "run_id": plan.run_id,
        "slots": [slot_to_dict(s) for s in plan.slots],
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "summary": {
            "hard_required_total": len(HARD_REQUIRED_SLOTS),
            "hard_required_validated": sum(
                1 for s in plan.slots
                if s.slot_type == P5SlotType.HARD_REQUIRED.value and s.status == P5SlotStatus.VALIDATED
            ),
            "conditional_total": len(CONDITIONAL_SLOTS),
            "conditional_validated": sum(
                1 for s in plan.slots
                if s.slot_type == P5SlotType.CONDITIONAL.value and s.status == P5SlotStatus.VALIDATED
            ),
            "conditional_evidence_gap": sum(
                1 for s in plan.slots
                if s.slot_type == P5SlotType.CONDITIONAL.value and s.status == P5SlotStatus.EVIDENCE_GAP
            ),
            "needs_user_input_count": sum(
                1 for s in plan.slots if s.status == P5SlotStatus.NEEDS_USER_INPUT
            ),
        },
        "can_be_completed": can_complete,
        "completion_reason": reason,
        "blocked_reason": plan.blocked_reason,
    }
