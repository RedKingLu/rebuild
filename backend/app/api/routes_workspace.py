"""Workspace aggregate API route."""

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects/{project_id}", tags=["workspace"])


@router.get("/workspace")
async def get_workspace(project_id: str, include: str | None = None):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    aggregate = svc.workspace_service.aggregate(project_id, include=include)
    svc.trace_writer.write("workspace_action", action="get_workspace",
                           summary=f"Workspace aggregate for {project_id}", project_id=project_id)
    return SuccessEnvelope(data=aggregate, meta=Meta())
