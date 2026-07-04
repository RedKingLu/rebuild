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
            # B-PLAN-1 (D-025/D-026): pre-execution plan review. Manual/Plan modes
            # pause here so the user审核阶段计划 BEFORE any stage action executes.
            _mode = (metadata or {}).get("mode", "plan")
            reason = f"{stage} 阶段计划待审核（{_mode} 模式）：审阅计划后决定是否执行阶段动作"
            summary = (f"{stage} 阶段起始计划已生成（目标 / 验收标准 / 计划动作）。"
                       f"请审阅后决定：批准执行 / 请求修改 / 拒绝。")
            retry_action = None
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
