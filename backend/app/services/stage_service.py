"""Stage service — read-only stage state + stage-plan persistence + real promotion.

R10 T12: get_stages / submit_stage_plan are REAL — stage plans persist to the
`stage_plan` table (models/stage_plan.py, T4) and stages report their real
stage_plan_ref. promote() routes through the real Gate. (Was R4 mock.)
"""

from typing import Optional
import uuid

from sqlalchemy.orm import Session

from app.schemas.stage import StageResponse, StagePlanRequest, PromotionRequest, PromotionDecision
from app.core.status import STAGE_STATUSES


P_STAGES = [
    ("P0", "接入"),
    ("P1", "建档"),
    ("P2", "评估"),
    ("P3", "规划"),
    ("P4", "执行"),
    ("P5", "验证"),
    ("P6", "交付"),
]


class StageService:
    def __init__(self, services):
        self._svc = services

    def _db(self) -> Session:
        from app.core.database import get_session
        return get_session()

    def _latest_stage_plan(self, db: Session, project_id: str, stage_code: str):
        """Latest StagePlan for (project, stage) by version then recency (real ref)."""
        from app.models.stage_plan import StagePlan
        return (db.query(StagePlan)
                .filter(StagePlan.project_id == project_id,
                        StagePlan.stage == stage_code.lower())
                .order_by(StagePlan.version.desc(), StagePlan.created_at.desc())
                .first())

    def get_stages(self, project_id: str, run_id: str) -> list[StageResponse]:
        """Return all P0-P6 stages for a run, with real stage_plan_ref from DB."""
        run = self._svc.run_service.get(run_id)
        db = self._db()
        try:
            stages = []
            for code, name in P_STAGES:
                status = "not_enabled"
                if run and run.stage_status:
                    # stage_status keys are written both cased (create=lower, sync=upper) — tolerate both
                    status = (run.stage_status.get(code)
                              or run.stage_status.get(code.lower())
                              or "not_enabled")
                sp = self._latest_stage_plan(db, project_id, code)
                stages.append(StageResponse(
                    stage_code=code,
                    stage_name=f"{code} {name}",
                    stage_status=status,
                    stage_plan_ref=sp.stage_plan_id if sp else None,
                    source_status="real",
                    transition_mode="real",
                ))
            return stages
        finally:
            db.close()

    def get_stage(self, project_id: str, run_id: str, stage_code: str) -> Optional[StageResponse]:
        stages = self.get_stages(project_id, run_id)
        for s in stages:
            if s.stage_code.upper() == stage_code.upper():
                return s
        return None

    def submit_stage_plan(self, project_id: str, run_id: str, stage: str, req: StagePlanRequest) -> dict:
        """Persist a submitted stage plan to the stage_plan table (real, R10 T12)."""
        from app.models.stage_plan import StagePlan
        db = self._db()
        try:
            sp = StagePlan(
                project_id=project_id,
                run_id=run_id or None,
                stage=(stage or "").lower() or None,
                plan_status="under_review",
                plan_summary=req.plan_summary,
                plan_detail=req.plan_detail or {},
            )
            db.add(sp)
            db.commit()
            db.refresh(sp)
            return {
                "stage_plan_id": sp.stage_plan_id,
                "project_id": project_id,
                "run_id": run_id,
                "stage": stage,
                "plan_summary": sp.plan_summary,
                "status": sp.plan_status,
                "version": sp.version,
                "source_status": "real",
                "transition_mode": "real",
            }
        finally:
            db.close()

    def promote(self, project_id: str, run_id: str, stage: str, req: PromotionDecision,
                drive_promotion: bool = True) -> dict:
        """Resolve the stage-promotion Gate for this stage (R9-3F).

        Locates the awaiting stage_promotion Gate for (project, stage) and routes
        the decision through GateService.decide(), which advances Project/Run state
        and writes Audit. Raises ValueError on illegal decision or missing Gate
        (routes map ValueError to HTTP 400).

        W9: when the decision is already driving a LangGraph checkpoint thread
        (thread_id=run_id), the caller passes drive_promotion=False so the graph
        advances the stage (single thread, no double-advancement); GateService
        still records the decision + Audit.
        """
        from app.schemas.gate import GateDecisionRequest

        gate = None
        for g in self._svc.gate_service.list_by_project(project_id):
            if (g.stage or "").lower() == (stage or "").lower() \
                    and g.gate_type == "stage_promotion" \
                    and g.gate_status == "waiting_decision":
                gate = g
                break
        if gate is None:
            gate = self._svc.gate_service.get_active(project_id)
        if gate is None:
            raise ValueError(f"无待决 Gate 可决策（project={project_id}, stage={stage}）")

        g, audit = self._svc.gate_service.decide(
            gate.gate_id, GateDecisionRequest(decision=req.decision, reason=req.reason),
            drive_promotion=drive_promotion,
        )
        return {
            "promotion_id": f"promo-{uuid.uuid4().hex[:8]}",
            "gate_id": gate.gate_id,
            "from_stage": stage,
            "decision": req.decision,
            "gate_status": g.gate_status,
            "audit_ref": g.audit_ref,
            "transition_mode": "real",
        }
