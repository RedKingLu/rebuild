"""RealGateBackend — graph GateBackend adapter over the DB-persisted GateService (R9-5-1, T9/T10).

The graph drives stage transitions via conditional edges, so decide() is called with
drive_promotion=False (record decision + Audit only; graph advances the stage).
"""

from __future__ import annotations

import logging
from typing import List, Optional

from app.dependencies import get_services
from app.schemas.gate import GateDecisionRequest

logger = logging.getLogger("rebuild.gate_backend")


def _read_gate_brief(project_id: str, artifact_refs: List[str]) -> Optional[dict]:
    """Read the real Gate Brief report (if attached) so the promotion Gate summary
    reflects real execution (R17.3-6 WP-2, D-101). Returns None when absent/unreadable."""
    import json
    from app.services.workspace_service import workspace_path
    for ref in (artifact_refs or []):
        if ref and ref.endswith("_gate_brief.json"):
            try:
                p = workspace_path(project_id) / ref
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("gate_backend: 读取 Gate Brief 失败 ref=%s", ref, exc_info=True)
                return None
    return None


# ── B-V262-GATEREASON-HARDCODED：晋级 Gate 文案按独立验收 verdict 的真实取值分派 ──────
#
# 原缺陷（本文件旧 :105 行）：
#     reason = f"{stage} 阶段已完成并通过独立验收，请求阶段晋级{verdict_txt}"
# "已完成并通过独立验收"是**无条件断言**，真实 verdict 只被拼进后面括号里的 verdict_txt。
# 于是 verdict == "rework_required" 时产出：
#     「p2 阶段已完成并通过独立验收，请求阶段晋级（独立验收：rework_required，2 项问题）」
# —— 前半句断言通过、后半句写着需返工，**字面语义自相矛盾**。用户若照前半句批准，平台上
# 就留下一次伪造的"通过"记录（gate-401a97 的形态）。
#
# 为什么旧的返工分支没兜住：那一支（下方 `_rework_target`）的触发条件是
# metadata["rework_target_stage"] 非空，而该字段只由 nodes.py 的 make_work_node 在
# P5FailureRouter 判 p4_rework_required 时写入 ⇒ **只覆盖 P5**。P0~P4 任一阶段判
# rework_required 时全部落进上面那句硬编码文案（台账解除条件 ③ 要求覆盖 P0~P4）。
#
# 修法（解除条件 ①③，单一数据源）：**直接读已经拿在手里的 brief["validation_verdict"]
# ["verdict"]** 分派文案，不再靠 metadata 反推。verdict 的取值来源是
# validation_agent._verdict_for()（accepted / accepted_with_warning / rework_required）
# 与 acceptance_service 的 result（conditional_pass / blocked 等），此处按"是否代表通过"
# 二分，未登记取值一律走"结论无法识别"的诚实分支——**绝不默认成通过**。
_VERDICT_PHRASE: dict[str, tuple[str, bool]] = {
    # verdict → (阶段状态陈述, 是否代表"未通过")
    "accepted": ("已完成并通过独立验收", False),
    "accepted_with_warning": ("已完成，独立验收通过但带告警", False),
    "conditional_pass": ("已完成，独立验收有条件通过", False),
    "rework_required": ("未通过独立验收，判定需要返工", True),
    "rework": ("未通过独立验收，判定需要返工", True),
    "blocked": ("未通过独立验收，判定阻塞", True),
    "failed": ("未通过独立验收，判定失败", True),
    "gate_required": ("未取得独立验收通过结论，判定需人工裁决", True),
}
# 三种"没有可信通过结论"的兜底，全部按未通过处理（fail-closed 的文案版本：宁可让用户多看
# 一眼产物，不可让平台替验收结论下断言）。
_VERDICT_UNKNOWN = ("执行结束，但独立验收结论无法识别", True)
_VERDICT_ABSENT = ("执行结束，未取得独立验收结论", True)
# 解除条件 ④：无 brief 时的旧兜底文案是 `{stage} 小循环通过，请求阶段晋级` +
# `{stage} 阶段已完成并产出三类审核报告，请审阅后决策。`——"小循环通过"同样是在**断言一个
# 本函数并未读到任何证据的结论**（brief 读不到就意味着验收结论不可知）。同批改为诚实表述。
_VERDICT_NO_BRIEF = ("执行结束，但未能读取独立验收报告，验收结论未知", True)
_NOT_PASSED_WARNING = "批准即在验收未通过/未知的情况下晋级，请先查阅本阶段验收报告再决策。"


def _promotion_gate_texts(stage: str, brief: Optional[dict]) -> tuple[str, str]:
    """合成 stage_promotion Gate 的 (reason, summary)，文案由真实 verdict 决定。

    brief is None → 连验收报告都没读到（_VERDICT_NO_BRIEF）。
    brief 有但无 validation_verdict → _VERDICT_ABSENT。
    verdict 不在 _VERDICT_PHRASE → _VERDICT_UNKNOWN（**不当成通过**）。
    """
    if brief is None:
        phrase, not_passed = _VERDICT_NO_BRIEF
        reason = f"{stage} 阶段{phrase}，请求阶段晋级"
        return reason + f"。{_NOT_PASSED_WARNING}", (
            f"{stage} 阶段执行结束，但未能读取独立验收报告 —— {_NOT_PASSED_WARNING}")

    what = brief.get("what_happened") or f"{stage} 阶段已完成"
    vv = brief.get("validation_verdict") or {}
    verdict = str(vv.get("verdict") or "").strip().lower()
    if not vv or not verdict:
        phrase, not_passed = _VERDICT_ABSENT
        verdict_txt = ""
    else:
        phrase, not_passed = _VERDICT_PHRASE.get(verdict, _VERDICT_UNKNOWN)
        verdict_txt = f"（独立验收：{vv.get('verdict', '')}，{vv.get('issues_count', 0)} 项问题）"

    reason = f"{stage} 阶段{phrase}，请求阶段晋级{verdict_txt}"
    if not_passed:
        reason += f"。{_NOT_PASSED_WARNING}"
    notes = brief.get("honest_notes") or ""
    summary = what + (f" {notes}" if notes else "")
    if not_passed:
        summary = f"{summary} {_NOT_PASSED_WARNING}".strip()
    return reason, summary


def _project_name(project_id: str) -> str:
    """取项目名（欢迎语用）。失败返 None。"""
    try:
        from app.dependencies import get_services
        p = get_services().project_service.get(project_id)
        return p.name if p else None
    except Exception:
        return None


class RealGateBackend:
    def create(self, *, project_id: str, run_id: str, stage: str,
               artifact_refs: List[str],
               gate_type: str = "stage_promotion",
               metadata: Optional[dict] = None) -> str:
        gs = get_services().gate_service
        # Build reason and summary based on gate_type
        if gate_type == "source_pending":
            reason = (
                f"{stage} 源码导入失败：源码目录为空。"
                + (metadata.get("retry_hint", "") if metadata else "")
            )
            summary = (
                f"{stage} 阶段源码导入失败，请补充凭据或切换为手动导入后重新执行。"
            )
            retry_action = metadata.get("retry_action") if metadata else None
        elif gate_type == "plan_review":
            # R17-3: plan_review gate = 欢迎 + 确认开始（不显示计划内容）。
            # 用户同意后 agent 才开始工作 → 生成计划 → 创建 plan_presentation gate 展示计划。
            _proj = _project_name(project_id) or "当前项目"
            reason = (f"欢迎进入「{_proj}」工作台。{stage.upper()} 接入将导入源码、"
                      f"登记材料与初始风险，产出可信接入输入。")
            summary = f"欢迎进入「{_proj}」工作台，是否开始 {stage.upper()} 接入？"
            retry_action = None
        elif gate_type == "plan_presentation":
            # R17-3: 展示 agent 生成的接入计划，用户/Agent 审核后再执行。
            _proj = _project_name(project_id) or "当前项目"
            reason = (f"「{_proj}」{stage.upper()} 接入计划已生成。请审阅计划后决定"
                      f"是否执行（批准后 agent 将按计划执行阶段动作）。")
            summary = f"{stage.upper()} 接入计划已生成，请审阅后决定是否执行。"
            retry_action = None
        else:
            # V26.2 返工修复第 8 项：这个 Gate 若携带"判定需要返工"的显式 metadata
            # （nodes.py 的 make_work_node 只在 P5FailureRouter.route().p4_rework_required
            # ==True 时才会设置 metadata["rework_target_stage"]，不是从 verdict/文案反推），
            # 文案必须诚实写"需要返工"，不能落进下面"正常晋级"或"找不到 brief 的兜底罐头
            # 话"这两支——gate-401a97 的问题正是文案说"已完成并请求晋级"，而真实语义是
            # "验证失败、批准将退回 P4"，字面意思与实际后果完全矛盾。这是与 A 部分
            # （_read_gate_brief 命中率修复）同批但不同的改动点：A 部分保证这里能读到
            # 真实 brief 内容；这里保证"是否要用返工文案"这一判断本身也是显式的。
            _rework_target = (metadata or {}).get("rework_target_stage")
            if _rework_target:
                brief = _read_gate_brief(project_id, artifact_refs)
                _real_reason = (metadata or {}).get("rework_reason") or "P5 验证未通过"
                _what = (brief.get("what_happened") if brief else "") or f"{stage} 阶段未完成"
                reason = (f"{stage} 判定需要返工：{_real_reason}。{_what}"
                         f"批准将把项目退回 {_rework_target.upper()} 重新执行相关节点。")
                summary = (f"{stage} 判定需要返工（{_real_reason}），"
                          f"批准后将退回 {_rework_target.upper()} 重新执行，不会晋级到下一阶段。")
                retry_action = None
            else:
                # R17.3-6 WP-2 (AGT-02/D-101): 若 artifact_refs 含真实 Gate Brief 落盘报告，
                # 读其 what_happened / validation_verdict 合成真实 summary/reason（替换硬编码模板）。
                # B-V262-GATEREASON-HARDCODED：文案分派整体移到模块级 _promotion_gate_texts()，
                # 由真实 verdict 决定"通过/未通过/未知"，见该函数上方的根因说明。
                reason, summary = _promotion_gate_texts(stage, _read_gate_brief(project_id, artifact_refs))
                retry_action = None
        gate = gs.create(
            project_id=project_id, run_id=run_id or "", stage=stage,
            gate_type=gate_type,
            reason=reason,
            summary=summary,
            options=(["update_source", "switch_to_manual"] if gate_type == "source_pending"
                     else ["approve", "reject", "request_changes"]),
            artifact_refs=artifact_refs or [],
            # WP-6: set checkpoint_ref = run_id (thread_id == run_id in LangGraph checkpoint)
            checkpoint_ref=run_id or None,
            interrupt_ref=run_id or None,
        )
        # Surface retry_action in gate reason so SSE body contains it
        if retry_action and gate_type == "source_pending":
            try:
                from app.core.database import get_session
                from app.models.gate import Gate
                db = get_session()
                try:
                    g = db.get(Gate, gate.gate_id)
                    if g:
                        g.reason = f"{reason} [retry_action={retry_action}]"
                        db.commit()
                finally:
                    db.close()
            except Exception as e:
                logger.warning("gate retry_action DB update failed gate_id=%s: %s", gate.gate_id, e)
        return gate.gate_id

    def decide(self, *, gate_id: str, decision: str) -> None:
        gs = get_services().gate_service
        # drive_promotion=False: the LangGraph gate node drives stage transitions.
        gs.decide(gate_id, GateDecisionRequest(decision=decision), drive_promotion=False)

    def attach_artifact_refs(self, *, gate_id: str, refs: List[str]) -> None:
        """GATE-02 (R17.3-6 WP-5): 向已存在的 Gate 合并补挂 artifact_refs（去重）。

        用于 make_work_node 复用 P6 handler 创建的唯一权威最终 Gate（D-023，
        _create_p6_final_gate）时，把三类审核报告 + Agent 侧材料补挂到同一 Gate，
        使用户在唯一 Gate 上看到完整审核材料——从而消除 P6 重复建 Gate（原
        handler p6_final_gate + make_work_node promotion gate 两个 stage_promotion）。
        """
        if not gate_id or not refs:
            return
        try:
            from app.core.database import get_session
            from app.models.gate import Gate
            db = get_session()
            try:
                g = db.get(Gate, gate_id)
                if g is None:
                    return
                existing = list(g.artifact_refs or [])
                merged = existing + [r for r in refs if r and r not in existing]
                if merged != existing:
                    g.artifact_refs = merged
                    db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("gate attach_artifact_refs failed gate_id=%s: %s", gate_id, e,
                           exc_info=True)

    def find_stage_gate(self, *, project_id: str, run_id: str, stage: str,
                        gate_type: str) -> Optional[dict]:
        """Find the latest gate of a given type for (project, run, stage).

        Used by the plan-review flow (B-PLAN-1) to stay idempotent across the
        interrupt/resume re-run of the work node: on resume the node re-executes
        from the top, so we must reuse the already-created plan_review gate (and
        skip when it is already decided) instead of creating a duplicate.
        Returns {gate_id, gate_status, decision} or None.
        """
        gs = get_services().gate_service
        match = None
        for g in gs.list_by_project(project_id):
            if (g.stage or "").lower() == (stage or "").lower() \
                    and g.gate_type == gate_type \
                    and (g.run_id or "") == (run_id or ""):
                match = g  # list_by_project is ordered by gate_id → last = latest
        if match is None:
            return None
        return {"gate_id": match.gate_id, "gate_status": match.gate_status,
                "decision": match.decision}
