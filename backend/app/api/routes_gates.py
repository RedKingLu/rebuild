"""Gate, Policy Check, and Risk Assessment API routes."""

import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services

logger = logging.getLogger("rebuild.routes_gates")
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


def _stage_has_real_artifact(svc, run_id: str, stage: str, artifact_refs: list | None) -> bool:
    """校验某 run 在某阶段是否有真实产物（task_graph 存在 OR artifact_refs 非空）。"""
    # 1) artifact_refs 非空（gate 自身携带的产物引用）
    if artifact_refs:
        return True
    # 2) task_graph 表存在该 run+stage
    try:
        db = svc.run_service._db()
        try:
            from app.models.task_graph import TaskGraph
            return (
                db.query(TaskGraph)
                .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage)
                .limit(1)
                .count()
                > 0
            )
        finally:
            db.close()
    except Exception as exc:
        import logging
        logging.getLogger("rebuild.routes_gates").warning("_stage_has_real_artifact 降级放行: %s", exc)
        return True


@router.post("/gates/{gate_id}/decision")
async def decide_gate(project_id: str, gate_id: str, req: GateDecisionRequest):
    """Resolve a Gate decision. WP-6: when a LangGraph checkpoint thread exists for
    gate.run_id, drives FlowRuntime.resume (graph advances stage); GateService records
    with drive_promotion=False to avoid double-advancement. Non-graph gates advance
    directly (drive_promotion=True)."""
    from app.graph.runtime import get_flow_runtime, graph_thread_active
    from app.api.routes_stages import _ensure_graph_task, _run_graph_bg

    svc = _svc()
    # Look up gate first to get run_id (needed for graph thread check)
    gate = svc.gate_service.get(gate_id)
    if gate is None:
        raise HTTPException(404, f"Gate {gate_id} not found")

    run_id = gate.run_id or ""
    graph_driven = False

    # R17-2 V-R17-1B-2/P1: gate 晋级强绑阶段产物。对 stage_promotion gate，晋级前校验
    # 该 run+stage 有真实产物（task_graph 存在 或 artifact_refs 非空），杜绝空壳晋级。
    # 置于 decide() 之前，确保 graph 路径和 direct 路径都强制校验。
    if gate.gate_type == "stage_promotion" and run_id:
        if not _stage_has_real_artifact(svc, run_id, gate.stage or "", gate.artifact_refs):
            raise HTTPException(
                422,
                f"阶段 {gate.stage} 晋级被拒绝：run {run_id} 在该阶段无真实产物"
                f"（task_graph 未创建且 artifact_refs 为空）。请先完成阶段执行再申请晋级。"
            )

    # R17-6: if gate has a checkpoint_ref (graph-created) and thread is still paused,
    # fire-and-forget the graph resume; HTTP returns immediately. DB sync happens in
    # _run_graph_bg once the graph actually completes.
    if (gate.checkpoint_ref or run_id) and run_id:
        try:
            if await graph_thread_active(run_id):
                _ensure_graph_task(_run_graph_bg(run_id, req.decision, project_id, gate.stage or ""))
                graph_driven = True
        except Exception as exc:
            import logging
            logging.getLogger("rebuild.routes_gates").warning(
                "graph task launch failed for gate %s run %s: %s", gate_id, run_id, exc, exc_info=True
            )
            graph_driven = False

    try:
        gate_resp, audit = svc.gate_service.decide(gate_id, req, drive_promotion=not graph_driven)
    except ValueError as e:
        # R17-2 V-R17-1B-2: 无产物晋级拒绝统一 422（非法值仍 400）
        code = 422 if "无真实产物" in str(e) else 400
        raise HTTPException(code, str(e))
    if gate_resp is None:
        raise HTTPException(404, f"Gate {gate_id} not found")

    svc.trace_writer.write("gate_event", action="decide_gate",
                           summary=f"Gate {gate_id} decision: {req.decision} "
                                   f"({'graph-driven' if graph_driven else 'direct'})",
                           project_id=project_id)
    return SuccessEnvelope(
        data={"gate": gate_resp.model_dump(), "audit": audit, "graph_driven": graph_driven,
              "transition_mode": "real_background" if graph_driven else "direct"},
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
