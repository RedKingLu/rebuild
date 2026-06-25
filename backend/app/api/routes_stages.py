"""Stage API routes."""

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.stage import StagePlanRequest, PromotionRequest, PromotionDecision
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects/{project_id}/runs/{run_id}/stages", tags=["stages"])


def _svc():
    return get_services()


@router.get("")
async def list_stages(project_id: str, run_id: str):
    svc = _svc()
    stages = svc.stage_service.get_stages(project_id, run_id)
    svc.trace_writer.write("state_change", action="list_stages",
                           summary=f"Listed {len(stages)} stages", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data={"stages": [s.model_dump() for s in stages]}, meta=Meta())


@router.get("/{stage}")
async def get_stage(project_id: str, run_id: str, stage: str):
    svc = _svc()
    s = svc.stage_service.get_stage(project_id, run_id, stage)
    if s is None:
        raise HTTPException(404, f"Stage {stage} not found in run {run_id}")
    svc.trace_writer.write("state_change", action="get_stage",
                           summary=f"Got stage {stage}", project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=s, meta=Meta())


@router.post("/{stage}/stage-plan")
async def submit_stage_plan(project_id: str, run_id: str, stage: str, req: StagePlanRequest):
    svc = _svc()
    result = svc.stage_service.submit_stage_plan(project_id, run_id, stage, req)
    svc.trace_writer.write("state_change", action="submit_stage_plan",
                           summary=f"Submitted stage plan for {stage}", project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=result, meta=Meta())


@router.post("/{stage}/promotion-gate")
async def create_promotion_gate(project_id: str, run_id: str, stage: str, req: PromotionRequest):
    svc = _svc()
    gate = svc.gate_service.create(
        project_id=project_id, run_id=run_id, stage=stage,
        gate_type="stage_promotion", reason=f"Stage promotion to {req.target_stage}",
        summary=f"请求晋级至 {req.target_stage}", options=["approve", "reject", "request_changes"],
    )
    svc.trace_writer.write("gate_event", action="create_promotion_gate",
                           summary=f"Created promotion gate {gate.gate_id}", project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=gate, meta=Meta())


@router.post("/{stage}/promotion-decision")
async def decide_promotion(project_id: str, run_id: str, stage: str, req: PromotionDecision):
    svc = _svc()
    result = svc.stage_service.promote(project_id, run_id, stage, req)
    svc.trace_writer.write("gate_event", action="decide_promotion",
                           summary=f"Promotion decision: {req.decision}", project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=result, meta=Meta())
