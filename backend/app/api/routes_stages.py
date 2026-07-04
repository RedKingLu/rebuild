"""Stage API routes."""

import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.stage import StagePlanRequest, PromotionRequest, PromotionDecision
from app.schemas.common import SuccessEnvelope, Meta

logger = logging.getLogger("rebuild.routes_stages")

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
    """Resolve a stage-promotion Gate. W9: unified single decision kernel
    (GateService.decide). When a paused LangGraph checkpoint thread exists for this
    run, the decision drives FlowRuntime.resume (thread_id=run_id) — the graph node
    calls _gate_backend.decide() internally, so stage_service.promote() is SKIPPED
    (no double-advancement, no stale-gate fallback). Non-graph runs fall back to
    stage_service.promote(drive_promotion=True) for direct advancement."""
    import uuid as _uuid
    from app.graph.runtime import get_flow_runtime, graph_thread_active
    from app.services.gate_service import VALID_DECISIONS

    svc = _svc()

    # P1-B: validate decision at the entry BEFORE any graph resume — an illegal
    # value must not drive the graph (which would rebuild a duplicate stage_promotion
    # Gate) nor return 200. Mirrors GateService.decide / routes_gates 400 semantics.
    decision_norm = (req.decision or "").strip().lower()
    if decision_norm not in VALID_DECISIONS:
        raise HTTPException(
            400, f"非法晋级决策：{req.decision!r}（允许 {sorted(VALID_DECISIONS)}）")


    graph_driven = False
    graph_state = None
    if await graph_thread_active(run_id):
        try:
            graph_state = await get_flow_runtime().resume(run_id, req.decision)
            graph_driven = True
        except Exception as e:
            logger.warning("graph resume failed for run=%s: %s", run_id, e)
            graph_driven = False

    if graph_driven and graph_state:
        # Graph handled the gate decision internally (make_gate_node → _gate_backend.decide).
        # Sync DB from graph state: set current_stage and clear active_gate.
        nxt = graph_state.get("current_stage")
        if nxt:
            try:
                svc.project_service.update(project_id, current_stage=nxt, active_gate="")
            except Exception as e:
                logger.warning("DB sync current_stage failed project=%s: %s", project_id, e)
        for st, status in (graph_state.get("stage_status") or {}).items():
            try:
                svc.run_service.set_stage_status(run_id, st, status)
            except Exception as e:
                logger.warning("DB sync stage_status failed run=%s stage=%s: %s", run_id, st, e)
        result = {
            "promotion_id": f"promo-{_uuid.uuid4().hex[:8]}",
            "gate_id": "",
            "from_stage": stage,
            "decision": req.decision,
            "gate_status": {
                "approve": "approved", "reject": "rejected",
                "request_changes": "changes_requested"}.get(req.decision, "unknown"),
            "audit_ref": None,
            "transition_mode": "real",
        }
    else:
        # Non-graph (or graph resume failed with partial side-effects): direct path.
        # Guard: if graph side-effects already decided the stage gate (e.g. resume ran
        # the graph but failed on checkpoint save AFTER the gate was decided), do NOT
        # call stage_service.promote() again — just sync the project stage from the
        # gate's implied next stage to avoid double-advancement.
        stage_gate_already_decided = False
        already_decided_gate = None
        try:
            for gx in svc.gate_service.list_by_project(project_id):
                if (gx.stage or "").lower() == stage.lower() \
                        and gx.gate_type == "stage_promotion" \
                        and gx.gate_status != "waiting_decision" \
                        and gx.run_id == run_id:
                    stage_gate_already_decided = True
                    already_decided_gate = gx
                    break
        except Exception as e:
            logger.warning("gate already-decided check failed project=%s stage=%s: %s", project_id, stage, e)

        if stage_gate_already_decided and already_decided_gate:
            # Graph ran the gate decision but failed to save checkpoint; sync DB.
            _dec = (already_decided_gate.decision or req.decision or "").lower()
            from app.services.gate_service import STAGE_ORDER as _SO
            try:
                _cur = stage.lower()
                if _dec == "approve":
                    _nxt = _SO[_SO.index(_cur) + 1] if _cur in _SO else _cur
                    svc.project_service.update(project_id, current_stage=_nxt, active_gate="")
                    graph_driven = True  # signal that stage was advanced (idiomatic)
                elif _dec in ("reject", "request_changes"):
                    svc.project_service.update(project_id, active_gate="")
                    graph_driven = True
            except Exception as e:
                logger.warning("DB sync after already-decided gate failed project=%s: %s", project_id, e)
            result = {
                "promotion_id": f"promo-{_uuid.uuid4().hex[:8]}",
                "gate_id": already_decided_gate.gate_id,
                "from_stage": stage,
                "decision": _dec,
                "gate_status": already_decided_gate.gate_status,
                "audit_ref": already_decided_gate.audit_ref,
                "transition_mode": "real",
            }
        else:
            try:
                result = svc.stage_service.promote(
                    project_id, run_id, stage, req, drive_promotion=True)
            except ValueError as e:
                raise HTTPException(400, str(e))

    result["graph_driven"] = graph_driven

    # UX-5: on reject / request_changes, persist the reason to a rework-notes artifact so
    # the rework (Acceptance) agent knows exactly why this attempt failed. Best-effort — a
    # notes write failure must not fail the decision itself.
    if req.decision in ("reject", "request_changes") and (req.reason or "").strip():
        try:
            from app.services.workspace_service import append_rework_notes
            result["rework_notes_ref"] = append_rework_notes(
                project_id, stage, req.decision, req.reason.strip())
        except Exception as e:
            logger.warning("rework-notes write failed project=%s stage=%s: %s",
                           project_id, stage, e)

    svc.trace_writer.write("gate_event", action="decide_promotion",
                           summary=f"Promotion decision: {req.decision} "
                                   f"({'graph-driven' if graph_driven else 'direct'})",
                           project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=result, meta=Meta())


# ── UX-5 / UX-4 fix: TaskGraph node status for the task-overview top bar ─

@router.get("/{stage}/taskgraph")
async def get_taskgraph(project_id: str, run_id: str, stage: str):
    """Return the active TaskGraph (if any) for (project, run, stage) with per-node
    runtime status — the honest source for the UX-4 task-overview bar. Responds with an
    empty node list (no active graph) rather than fabricating tasks: the frontend falls back
    to the live task-context + real-time status when this is empty."""
    svc = _svc()
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph, TaskNode, TaskGraphRun
    from app.models.task_node_run import TaskNodeRun
    db = get_session()
    try:
        # Latest non-superseded graph run for this stage.
        graph_run = (
            db.query(TaskGraphRun)
            .filter(TaskGraphRun.run_id == run_id, TaskGraphRun.stage == stage)
            .order_by(TaskGraphRun.started_at.desc().nullslast()
                      if hasattr(TaskGraphRun, "started_at") else TaskGraphRun.task_graph_run_id.desc())
            .first()
        )
        graph_run_id = graph_run.task_graph_run_id if graph_run else None
        graph_status = graph_run.graph_status if graph_run else None
        # Graph definition: prefer the run's graph_id, else latest non-superseded for the stage.
        graph_id = graph_run.task_graph_id if graph_run else None
        graph = None
        if graph_id is not None:
            graph = db.get(TaskGraph, graph_id)
        if graph is None:
            graph = (
                db.query(TaskGraph)
                .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage,
                        TaskGraph.graph_status != "superseded")
                .order_by(TaskGraph.task_graph_id.desc())
                .first()
            )
        if graph is None:
            return SuccessEnvelope(
                data={"graph_id": None, "graph_run_id": None, "graph_status": None,
                      "nodes": []},
                meta=Meta(source_status="real"),
            )
        nodes_def = (
            db.query(TaskNode)
            .filter(TaskNode.task_graph_id == graph.task_graph_id)
            .order_by(TaskNode.node_id)
            .all()
        )
        # Latest run per node definition (if a run exists).
        node_status: dict[str, dict] = {}
        if graph_run_id:
            runs = (
                db.query(TaskNodeRun)
                .filter(TaskNodeRun.task_graph_run_id == graph_run_id)
                .order_by(TaskNodeRun.started_at.asc().nullslast())
                .all()
            )
            for r in runs:
                node_status[r.node_id] = {"node_status": r.node_status,
                                          "retry_count": r.retry_count or 0}
        nodes = []
        for nd in nodes_def:
            st = node_status.get(nd.node_id, {})
            nodes.append({
                "node_id": nd.node_id,
                "node_type": nd.node_type or "execution",
                "title": nd.title or nd.node_id,
                "status": st.get("node_status") or "pending",
                "retry_count": st.get("retry_count", 0),
            })
        return SuccessEnvelope(
            data={"graph_id": graph.task_graph_id, "graph_run_id": graph_run_id,
                  "graph_status": graph_status, "nodes": nodes},
            meta=Meta(source_status="real"),
        )
    finally:
        db.close()
