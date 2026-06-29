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
    """Resolve a Gate decision. WP-6: when a LangGraph checkpoint thread exists for
    gate.run_id, drives FlowRuntime.resume (graph advances stage); GateService records
    with drive_promotion=False to avoid double-advancement. Non-graph gates advance
    directly (drive_promotion=True)."""
    from app.graph.runtime import get_flow_runtime, graph_thread_active

    svc = _svc()
    # Look up gate first to get run_id (needed for graph thread check)
    gate = svc.gate_service.get(gate_id)
    if gate is None:
        raise HTTPException(404, f"Gate {gate_id} not found")

    run_id = gate.run_id or ""
    graph_driven = False
    graph_state = None

    # WP-6: if gate has a checkpoint_ref (graph-created) and thread is still paused, resume graph
    if (gate.checkpoint_ref or run_id) and run_id:
        try:
            if await graph_thread_active(run_id):
                graph_state = await get_flow_runtime().resume(run_id, req.decision)
                graph_driven = True
        except Exception as exc:
            import logging
            logging.getLogger("rebuild.routes_gates").warning(
                "graph resume failed for gate %s run %s: %s", gate_id, run_id, exc, exc_info=True
            )
            graph_driven = False

    try:
        gate_resp, audit = svc.gate_service.decide(gate_id, req, drive_promotion=not graph_driven)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if gate_resp is None:
        raise HTTPException(404, f"Gate {gate_id} not found")

    # Sync project/run DB from graph state if graph drove the decision
    if graph_driven and graph_state:
        nxt = (graph_state if isinstance(graph_state, dict) else {}).get("current_stage")
        if nxt:
            try:
                svc.project_service.update(project_id, current_stage=nxt, active_gate="")
            except Exception:
                pass
        for st, status in ((graph_state if isinstance(graph_state, dict) else {}).get("stage_status") or {}).items():
            try:
                svc.run_service.set_stage_status(run_id, st, status)
            except Exception:
                pass

    svc.trace_writer.write("gate_event", action="decide_gate",
                           summary=f"Gate {gate_id} decision: {req.decision} "
                                   f"({'graph-driven' if graph_driven else 'direct'})",
                           project_id=project_id)
    return SuccessEnvelope(
        data={"gate": gate_resp.model_dump(), "audit": audit, "graph_driven": graph_driven},
        meta=Meta(source_status="real"),
    )


@router.post("/policy/check")
async def policy_check(project_id: str, req: PolicyCheckRequest):
    svc = _svc()
    result = svc.gate_service.policy_check(req)
    svc.trace_writer.write("policy_check", action="policy_check",
                           summary=f"Policy check: allowed={result.allowed}", project_id=project_id)
    return SuccessEnvelope(data=result, meta=Meta(source_status="real"))


@router.post("/risk/assess")
async def risk_assess(project_id: str, req: RiskAssessmentRequest):
    svc = _svc()
    result = svc.gate_service.risk_assess(req)
    svc.trace_writer.write("policy_check", action="risk_assess",
                           summary=f"Risk assessment: {result.risk_level}", project_id=project_id)
    return SuccessEnvelope(data=result, meta=Meta(source_status="real"))
