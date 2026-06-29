"""Graph driver endpoints — drive the P0-P6 LangGraph over HTTP (R9-5-1, T10/T12 增量).

Additive endpoints (do NOT replace the legacy onboarding/profile endpoints yet — the full
cutover + gate-decision→resume coordinate with R9-5-7 HITL). These expose the real compiled
graph: start a run (p0_work → pause at promotion Gate), resume after a Gate decision, read the
checkpointed state. thread_id == run_id; state survives restart (checkpoint).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_services
from app.schemas.common import Meta, SuccessEnvelope
from app.schemas.run import RunCreate
from app.services.project_service import ProjectService
from app.services.run_service import RunService
from app.graph.runtime import get_flow_runtime

router = APIRouter(prefix="/projects", tags=["graph"])

_VALID_DECISIONS = {"approve", "reject", "request_changes"}


class GraphStartRequest(BaseModel):
    run_goal: str | None = None
    execution_mode: str = "plan"
    source_type: str | None = None


class GraphResumeRequest(BaseModel):
    run_id: str
    decision: str


def _summary(state: dict) -> dict:
    state = state or {}
    return {
        "current_stage": state.get("current_stage"),
        "run_status": state.get("run_status"),
        "stage_status": state.get("stage_status", {}),
        "paused": state.get("__interrupt__") is not None,
        "pending_gate": state.get("pending_gate"),
        "last_decision": state.get("last_decision"),
        "artifacts_count": len(state.get("artifacts", [])),
        "events_count": len(state.get("events", [])),
        "graph_capability_status": get_flow_runtime().capability_status(),
    }


def _src_type(project) -> str:
    st = project.source_type
    return st.value if hasattr(st, "value") else str(st)


@router.post("/{project_id}/graph/start")
async def graph_start(project_id: str, req: GraphStartRequest, db: Session = Depends(get_db)):
    """Create a Run (graph thread) and start the P0-P6 graph; pauses at the P0 promotion Gate."""
    svc = ProjectService(db)
    deps = get_services()
    project = svc.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    run = RunService(deps).create(
        project_id,
        RunCreate(run_goal=req.run_goal or f"P0-P6: {project.name}", mode=req.execution_mode),
    )
    svc.update(project_id, current_run_id=run.run_id, current_stage="p0")

    init_state = {
        "run_id": run.run_id, "project_id": project_id,
        "run_goal": run.run_goal, "run_status": "running",
        "source_type": req.source_type or _src_type(project),
        "source_config": project.source_config or {},
        "execution_mode": req.execution_mode,
        "stage_status": {"p0": "in_progress"},
    }
    state = await get_flow_runtime().start(run.run_id, init_state)
    return SuccessEnvelope(data={"run_id": run.run_id, **_summary(state)}, meta=Meta())


@router.post("/{project_id}/graph/resume")
async def graph_resume(project_id: str, req: GraphResumeRequest, db: Session = Depends(get_db)):
    """Resume a paused graph with a Gate decision (approve / reject / request_changes)."""
    decision = (req.decision or "").strip().lower()
    if decision not in _VALID_DECISIONS:
        raise HTTPException(400, f"非法决策：{req.decision!r}（允许 {sorted(_VALID_DECISIONS)}）")
    svc = ProjectService(db)
    if svc.get(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    state = await get_flow_runtime().resume(req.run_id, decision)
    return SuccessEnvelope(data={"run_id": req.run_id, **_summary(state)}, meta=Meta())


@router.get("/{project_id}/graph/state")
async def graph_state(project_id: str, run_id: str = Query(...), db: Session = Depends(get_db)):
    """Read the checkpointed graph state snapshot for a run (survives restart)."""
    snap = await get_flow_runtime().get_state(run_id)
    return SuccessEnvelope(data={"run_id": run_id, **_summary(snap.values)}, meta=Meta())
