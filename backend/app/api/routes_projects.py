"""Project API routes."""

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListResponse
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
async def list_projects(status: str | None = None):
    svc = get_services()
    projects = svc.project_service.list(status=status)
    # Write trace
    svc.trace_writer.write("state_change", action="list_projects", summary=f"Listed {len(projects)} projects")
    return SuccessEnvelope(
        data=ProjectListResponse(projects=projects, total=len(projects), meta=Meta()),
        meta=Meta(),
    )


@router.post("")
async def create_project(req: ProjectCreate):
    svc = get_services()
    project = svc.project_service.create(req)
    svc.trace_writer.write("state_change", action="create_project",
                           summary=f"Created project {project.project_id}", project_id=project.project_id)
    return SuccessEnvelope(data=project, meta=Meta())


@router.get("/{project_id}")
async def get_project(project_id: str):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc.trace_writer.write("state_change", action="get_project",
                           summary=f"Got project {project_id}", project_id=project_id)
    return SuccessEnvelope(data=project, meta=Meta())


@router.patch("/{project_id}")
async def update_project(project_id: str, req: ProjectUpdate):
    svc = get_services()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    project = svc.project_service.update(project_id, **updates)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc.trace_writer.write("state_change", action="update_project",
                           summary=f"Updated project {project_id}", project_id=project_id)
    return SuccessEnvelope(data=project, meta=Meta())


@router.post("/{project_id}/archive")
async def archive_project(project_id: str):
    svc = get_services()
    ok = svc.project_service.archive(project_id)
    if not ok:
        raise HTTPException(404, f"Project {project_id} not found")
    svc.trace_writer.write("state_change", action="archive_project",
                           summary=f"Archived project {project_id}", project_id=project_id)
    return SuccessEnvelope(data={"project_id": project_id, "archived": True}, meta=Meta())
