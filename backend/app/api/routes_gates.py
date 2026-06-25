"""Gate, Policy Check, and Risk Assessment API routes."""

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.gate import (
    GateDecisionRequest,
    PolicyCheckRequest, RiskAssessmentRequest,
    GateListResponse,
)
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects/{project_id}", tags=["gates"])


def _svc():
    return get_services()


@router.get("/gates")
async def list_gates(project_id: str):
    svc = _svc()
    gates = svc.gate_service.list_by_project(project_id)
    svc.trace_writer.write("gate_event", action="list_gates",
                           summary=f"Listed {len(gates)} gates", project_id=project_id)
    return SuccessEnvelope(
        data=GateListResponse(gates=gates, total=len(gates)),
        meta=Meta(),
    )


@router.get("/gates/active")
async def get_active_gate(project_id: str):
    svc = _svc()
    gate = svc.gate_service.get_active(project_id)
    svc.trace_writer.write("gate_event", action="get_active_gate",
                           summary=f"Active gate: {gate.gate_id if gate else 'none'}", project_id=project_id)
    return SuccessEnvelope(data=gate, meta=Meta())


@router.post("/gates")
async def create_gate(project_id: str, gate_data: dict):
    svc = _svc()
    gate = svc.gate_service.create(
        project_id=project_id,
        run_id=gate_data.get("run_id", ""),
        stage=gate_data.get("stage", ""),
        gate_type=gate_data.get("gate_type", "manual_confirmation"),
        reason=gate_data.get("reason", ""),
        risk_level=gate_data.get("risk_level", "L0"),
        summary=gate_data.get("summary", ""),
        options=gate_data.get("options"),
    )
    svc.trace_writer.write("gate_event", action="create_gate",
                           summary=f"Created gate {gate.gate_id}", project_id=project_id)
    return SuccessEnvelope(data=gate, meta=Meta())


@router.post("/gates/{gate_id}/decision")
async def decide_gate(project_id: str, gate_id: str, req: GateDecisionRequest):
    svc = _svc()
    gate, audit = svc.gate_service.decide(gate_id, req)
    if gate is None:
        raise HTTPException(404, f"Gate {gate_id} not found")
    svc.trace_writer.write("gate_event", action="decide_gate",
                           summary=f"Gate {gate_id} decision: {req.decision}", project_id=project_id)
    return SuccessEnvelope(
        data={"gate": gate.model_dump(), "audit": audit},
        meta=Meta(source_status="mock"),
    )


@router.post("/policy/check")
async def policy_check(project_id: str, req: PolicyCheckRequest):
    svc = _svc()
    result = svc.gate_service.policy_check(req)
    svc.trace_writer.write("policy_check", action="policy_check",
                           summary=f"Policy check: allowed={result.allowed}", project_id=project_id)
    return SuccessEnvelope(data=result, meta=Meta(source_status="mock"))


@router.post("/risk/assess")
async def risk_assess(project_id: str, req: RiskAssessmentRequest):
    svc = _svc()
    result = svc.gate_service.risk_assess(req)
    svc.trace_writer.write("policy_check", action="risk_assess",
                           summary=f"Risk assessment: {result.risk_level}", project_id=project_id)
    return SuccessEnvelope(data=result, meta=Meta(source_status="mock"))
