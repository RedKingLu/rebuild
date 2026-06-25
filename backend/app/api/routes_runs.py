"""Run API routes."""

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.run import RunCreate, RunResponse, RunResumeRequest, RunListResponse
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects/{project_id}/runs", tags=["runs"])


def _svc():
    return get_services()


@router.get("")
async def list_runs(project_id: str):
    svc = _svc()
    runs = svc.run_service.list_by_project(project_id)
    svc.trace_writer.write("state_change", action="list_runs",
                           summary=f"Listed {len(runs)} runs", project_id=project_id)
    return SuccessEnvelope(
        data=RunListResponse(runs=runs, total=len(runs), meta=Meta()),
        meta=Meta(),
    )


@router.post("")
async def create_run(project_id: str, req: RunCreate):
    svc = _svc()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    run = svc.run_service.create(project_id, req)
    svc.trace_writer.write("state_change", action="create_run",
                           summary=f"Created run {run.run_id}", project_id=project_id, run_id=run.run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.get("/{run_id}")
async def get_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.get(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="get_run",
                           summary=f"Got run {run_id}", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/start")
async def start_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.start(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="start_run",
                           summary=f"Started run {run_id} (mock transition)", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/pause")
async def pause_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.pause(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="pause_run",
                           summary=f"Paused run {run_id} (mock)", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/cancel")
async def cancel_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.cancel(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="cancel_run",
                           summary=f"Canceled run {run_id} (mock)", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/resume")
async def resume_run(project_id: str, run_id: str, req: RunResumeRequest):
    svc = _svc()
    run = svc.run_service.resume(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="resume_run",
                           summary=f"Resumed run {run_id} with decision={req.decision} (mock)", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())
