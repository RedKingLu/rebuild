"""Gate service — DB-persisted CRUD with mandatory Audit writing (R9 P1-2).

Replaces the in-memory _gates dict with SQLAlchemy Gate model.
"""

import logging as _logging
import uuid
_logger = _logging.getLogger("rebuild.gate_service")

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.gate import Gate
from app.schemas.gate import (
    GateResponse, GateDecisionRequest,
    PolicyCheckRequest, PolicyCheckResponse,
    RiskAssessmentRequest, RiskAssessmentResponse,
)

VALID_DECISIONS = {"approve", "reject", "request_changes"}
STAGE_ORDER = ["p0", "p1", "p2", "p3", "p4", "p5", "p6"]
_DECISION_TO_STATUS = {
    "approve": "approved",
    "reject": "rejected",
    "request_changes": "changes_requested",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gate_to_response(g: Gate) -> GateResponse:
    # P2-C: graph capability must be a REAL probe, never the schema default
    # "not_connected" (state.py 红线：不得写死 not_connected). "live" when the
    # StateGraph compiles, else "degraded" (公理4, no hardcode).
    try:
        from app.graph.runtime import graph_capability_probe
        graph_cap = graph_capability_probe()
    except Exception:
        graph_cap = "degraded"
    return GateResponse(
        gate_id=g.gate_id,
        gate_type=g.gate_type,
        gate_status=g.gate_status,
        project_id=g.project_id,
        run_id=g.run_id or "",
        stage=g.stage or "",
        reason=g.reason or "",
        risk_level=g.risk_level or "L0",
        summary=g.summary or "",
        options=g.options or ["approve", "reject"],
        recommended_option=None,
        decision=g.decision,
        decided_by=None,
        decided_at=g.decided_at.isoformat() if g.decided_at else None,
        artifact_refs=g.artifact_refs or [],
        evidence_refs=g.evidence_refs or [],
        trace_refs=g.trace_refs or [],
        audit_ref=g.audit_ref,
        source_status="real",
        transition_mode=g.transition_mode or "real",
        graph_capability_status=graph_cap,
        checkpoint_ref=g.checkpoint_ref,
        interrupt_ref=g.interrupt_ref,
    )


class GateService:
    def __init__(self, services):
        self._svc = services

    def _db(self) -> Session:
        from app.core.database import get_session
        return get_session()

    def list_by_project(self, project_id: str) -> list[GateResponse]:
        db = self._db()
        try:
            gates = db.query(Gate).filter(Gate.project_id == project_id).order_by(Gate.gate_id).all()
            return [_gate_to_response(g) for g in gates]
        finally:
            db.close()

    def get(self, gate_id: str) -> Optional[GateResponse]:
        db = self._db()
        try:
            g = db.get(Gate, gate_id)
            return _gate_to_response(g) if g else None
        finally:
            db.close()

    def get_active(self, project_id: str) -> Optional[GateResponse]:
        db = self._db()
        try:
            g = db.query(Gate).filter(
                Gate.project_id == project_id,
                Gate.gate_status == "waiting_decision",
            ).first()
            return _gate_to_response(g) if g else None
        finally:
            db.close()

    def create(self, project_id: str, run_id: str | None, stage: str,
               gate_type: str, reason: str = "", risk_level: str = "L0",
               summary: str = "", options: list[str] | None = None,
               artifact_refs: list[str] | None = None,
               evidence_refs: list[str] | None = None,
               checkpoint_ref: str | None = None,
               interrupt_ref: str | None = None) -> GateResponse:
        db = self._db()
        try:
            g = Gate(
                project_id=project_id,
                run_id=run_id or "",
                stage=stage,
                gate_type=gate_type,
                gate_status="waiting_decision",
                reason=reason,
                risk_level=risk_level,
                summary=summary,
                options=options or ["approve", "reject", "request_changes"],
                artifact_refs=artifact_refs or [],
                evidence_refs=evidence_refs or [],
                checkpoint_ref=checkpoint_ref,
                interrupt_ref=interrupt_ref,
            )
            db.add(g)
            db.commit()
            db.refresh(g)
            return _gate_to_response(g)
        finally:
            db.close()

    def decide(self, gate_id: str, req: GateDecisionRequest,
               drive_promotion: bool = True) -> tuple[Optional[GateResponse], Optional[dict]]:
        """Submit a Gate decision with full state advancement (R9 P1-2).

        drive_promotion: when False (LangGraph orchestration, R9-5-1), record the
        decision + Audit but DO NOT advance project/run stage here — the graph drives
        stage transitions via conditional edges (avoids double-advancement).
        """
        db = self._db()
        try:
            g = db.get(Gate, gate_id)
            if g is None:
                return None, None

            decision = (req.decision or "").strip().lower()
            if decision not in VALID_DECISIONS:
                raise ValueError(
                    f"非法 Gate 决策：{req.decision!r}（允许 {sorted(VALID_DECISIONS)}）"
                )

            g.gate_status = _DECISION_TO_STATUS[decision]
            g.decision = decision
            g.decided_at = datetime.now(timezone.utc)
            g.transition_mode = "real"
            db.commit()
            db.refresh(g)

            # Drive stage transition
            if g.gate_type == "stage_promotion" and drive_promotion:
                self._apply_promotion(g, decision)

            # Audit
            audit = self._svc.audit_writer.write(
                audit_type="gate_decision", gate_id=gate_id, risk_level=g.risk_level,
                action="gate_decision", decision=decision, reason=req.reason,
                project_id=g.project_id, run_id=g.run_id, stage=g.stage,
            )
            if audit and isinstance(audit, dict):
                g.audit_ref = audit.get("audit_id")
                db.commit()

            return _gate_to_response(g), audit
        finally:
            db.close()

    def _apply_promotion(self, g: Gate, decision: str) -> None:
        """Advance project/run state per a stage-promotion decision."""
        ps = self._svc.project_service
        try:
            project = ps.get(g.project_id)
        except Exception:
            _logger.warning(f"_apply_promotion: failed to get project {g.project_id}", exc_info=True)
            project = None
        cur = (g.stage or (getattr(project, "current_stage", None) if project else None) or "p0").lower()

        if decision == "approve":
            # R17-2 V-R17-1B-2/P1: gate 晋级强绑阶段产物。stage_promotion 前校验该
            # run+stage 有真实产物（task_graph 存在），杜绝空壳晋级。plan_review 豁免。
            if g.gate_type == "stage_promotion" and g.run_id:
                if not self._stage_has_real_artifact(g.run_id, cur):
                    raise ValueError(
                        f"阶段 {cur} 晋级被拒绝：run {g.run_id} 在该阶段无真实产物"
                        f"（task_graph 未创建）。请先完成阶段执行再申请晋级。"
                    )
            try:
                nxt = STAGE_ORDER[STAGE_ORDER.index(cur) + 1]
            except (ValueError, IndexError):
                nxt = cur
            if g.run_id:
                self._svc.run_service.set_stage_status(g.run_id, cur, "completed")
                if nxt != cur:
                    self._svc.run_service.set_stage_status(g.run_id, nxt, "in_progress")
            if project is not None:
                try:
                    ps.update(g.project_id, current_stage=nxt, active_gate="")
                except Exception:
                    _logger.warning(f"_apply_promotion: approve update failed for project {g.project_id}", exc_info=True)
        elif decision == "reject":
            if g.run_id:
                self._svc.run_service.set_stage_status(g.run_id, cur, "blocked")
            if project is not None:
                try:
                    ps.update(g.project_id, active_gate="")
                except Exception:
                    _logger.warning(f"_apply_promotion: reject update failed for project {g.project_id}", exc_info=True)
        elif decision == "request_changes":
            if g.run_id:
                self._svc.run_service.set_stage_status(g.run_id, cur, "changes_requested")
            if project is not None:
                try:
                    ps.update(g.project_id, active_gate="")
                except Exception:
                    _logger.warning(f"_apply_promotion: request_changes update failed for project {g.project_id}", exc_info=True)

    def _stage_has_real_artifact(self, run_id: str, stage: str) -> bool:
        """校验某 run 在某阶段是否有真实产物（task_graph 存在）。

        最小阈值：task_graph 表有 run_id+stage 记录。未来可加 artifact/output_code。
        """
        db = self._db()
        try:
            from app.models.task_graph import TaskGraph
            return (
                db.query(TaskGraph)
                .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage)
                .limit(1)
                .count()
                > 0
            )
        except Exception as exc:
            # task_graph 表不存在时（旧环境）降级放行，避免阻断
            _logger.warning("_stage_has_real_artifact 查询失败，降级放行: %s", exc)
            return True
        finally:
            db.close()

    def policy_check(self, req: PolicyCheckRequest) -> PolicyCheckResponse:
        from app.services.mode_policy import authorize_action, risk_for_action
        ctx = req.context or {}
        # Risk: explicit request value wins; else derive from action type (Q-5 single source).
        risk = req.risk_level if (req.risk_level and req.risk_level != "L0") else risk_for_action(req.action_type)
        result = authorize_action(
            mode=ctx.get("mode", "plan"),
            risk_level=risk,
            action=req.action_type or "policy_check",
            in_plan=bool(ctx.get("in_plan", False)),
        )
        return PolicyCheckResponse(
            check_id=f"pc-{uuid.uuid4().hex[:6]}",
            allowed=result["decision"] != "require_confirmation",
            reason=result["reason"],
            required_gate=result["decision"] == "require_confirmation",
            source_status="real",
        )

    def risk_assess(self, req: RiskAssessmentRequest) -> RiskAssessmentResponse:
        from app.services.mode_policy import authorize_action, risk_for_action
        ctx = req.context or {}
        # R9-5-7 T14: derive mode + risk from request context, not hardcoded plan/L1.
        mode = ctx.get("mode", "plan")
        risk = ctx.get("risk_level") or risk_for_action(req.action_type)
        result = authorize_action(
            mode=mode, risk_level=risk,
            action=req.action_type or "risk_assessment",
            in_plan=bool(ctx.get("in_plan", False)),
        )
        return RiskAssessmentResponse(
            assessment_id=f"ra-{uuid.uuid4().hex[:6]}",
            risk_level=result["risk_level"],
            summary=result["reason"],
            source_status="real",
        )
