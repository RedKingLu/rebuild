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
            # R17.3-6 WP-2 (AGT-02/D-101): 若 artifact_refs 含真实 Gate Brief 落盘报告，
            # 读其 what_happened / validation_verdict 合成真实 summary/reason（替换硬编码模板）。
            brief = _read_gate_brief(project_id, artifact_refs)
            if brief:
                what = brief.get("what_happened") or f"{stage} 阶段已完成"
                vv = brief.get("validation_verdict") or {}
                verdict_txt = ""
                if vv:
                    verdict_txt = f"（独立验收：{vv.get('verdict','')}，{vv.get('issues_count',0)} 项问题）"
                notes = brief.get("honest_notes") or ""
                reason = f"{stage} 阶段已完成并通过独立验收，请求阶段晋级{verdict_txt}"
                summary = what + (f" {notes}" if notes else "")
            else:
                reason = f"{stage} 小循环通过，请求阶段晋级"
                summary = f"{stage} 阶段已完成并产出三类审核报告，请审阅后决策。"
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
