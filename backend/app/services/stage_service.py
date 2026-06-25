"""Stage service — read-only stage state + mock promotion.

R4: Stage promotion is a mock transition (transition_mode="mock").
No real LangGraph stage node execution.
"""

from typing import Optional

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

    def get_stages(self, project_id: str, run_id: str) -> list[StageResponse]:
        """Return all P0-P6 stages for a run."""
        run = self._svc.run_service.get(run_id)
        stages = []
        for code, name in P_STAGES:
            status = "not_enabled"
            if run and run.stage_status:
                status = run.stage_status.get(code, "not_enabled")
            stages.append(StageResponse(
                stage_code=code,
                stage_name=f"{code} {name}",
                stage_status=status,
                source_status="mock",
                transition_mode="mock",
            ))
        return stages

    def get_stage(self, project_id: str, run_id: str, stage_code: str) -> Optional[StageResponse]:
        stages = self.get_stages(project_id, run_id)
        for s in stages:
            if s.stage_code.upper() == stage_code.upper():
                return s
        return None

    def submit_stage_plan(self, project_id: str, run_id: str, stage: str, req: StagePlanRequest) -> dict:
        """Mock stage plan submission."""
        import uuid
        return {
            "stage_plan_id": f"sp-{uuid.uuid4().hex[:8]}",
            "project_id": project_id,
            "run_id": run_id,
            "stage": stage,
            "plan_summary": req.plan_summary,
            "status": "submitted",
            "source_status": "mock",
            "transition_mode": "mock",
        }

    def promote(self, project_id: str, run_id: str, stage: str, req: PromotionDecision) -> dict:
        """Mock stage promotion — returns a gate creation stub."""
        return {
            "promotion_id": f"promo-{uuid.uuid4().hex[:8]}",
            "from_stage": stage,
            "decision": req.decision,
            "reason": req.reason,
            "gate_required": True,
            "source_status": "mock",
            "transition_mode": "mock",
        }
