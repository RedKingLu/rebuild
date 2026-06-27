"""Project API routes — DB-backed CRUD with Trace audit."""

import os
import uuid
import zipfile
import shutil
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.project_service import ProjectService
from app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectSourceUpdate,
    ProjectResponse, ProjectListResponse,
)
from app.schemas.common import SuccessEnvelope, Meta
from app.dependencies import get_services

router = APIRouter(prefix="/projects", tags=["projects"])


def _svc(db: Session = Depends(get_db)) -> ProjectService:
    return ProjectService(db)


@router.get("")
async def list_projects(
    status: str | None = Query(None, description="Filter by project status"),
    sort: str = Query("updated_at", description="Sort column"),
    order: str = Query("desc", description="Sort order (asc or desc)"),
    limit: int | None = Query(None, description="Max results"),
    offset: int = Query(0, description="Offset for pagination"),
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    projects = svc.list(status=status, sort=sort, order=order, limit=limit, offset=offset)
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="list_projects",
        summary=f"Listed {len(projects)} projects",
    )
    return SuccessEnvelope(
        data=ProjectListResponse(
            projects=[ProjectResponse(**svc.to_response(p)) for p in projects],
            total=len(projects),
            meta=Meta(),
        ),
        meta=Meta(),
    )


@router.post("")
async def create_project(
    req: ProjectCreate,
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    project = svc.create(req)
    svc_deps = get_services()

    # R8: Auto-initialize per-project workspace directory (D-050)
    try:
        from app.services.workspace_service import init_workspace
        init_workspace(project.project_id)
        svc.update(project.project_id, workspace_status="initialized")
    except Exception:
        pass  # Workspace init failure is non-fatal

    svc_deps.trace_writer.write(
        "state_change", action="create_project",
        summary=f"Created project {project.project_id}",
        project_id=project.project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    project = svc.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="get_project",
        summary=f"Got project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    req: ProjectUpdate,
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    project = svc.update(project_id, **updates)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="update_project",
        summary=f"Updated project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
):
    """Soft-delete (archive) a project."""
    svc = ProjectService(db)
    ok = svc.delete(project_id)
    if not ok:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="delete_project",
        summary=f"Archived project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data={"project_id": project_id, "archived": True},
        meta=Meta(),
    )


@router.post("/{project_id}/archive")
async def archive_project(
    project_id: str,
    db: Session = Depends(get_db),
):
    """Archive a project (legacy alias for soft-delete)."""
    svc = ProjectService(db)
    ok = svc.delete(project_id)
    if not ok:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="archive_project",
        summary=f"Archived project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data={"project_id": project_id, "archived": True},
        meta=Meta(),
    )


# ── ZIP upload: create project + upload source ──────────────────────

@router.post("/upload")
async def create_project_with_zip(
    name: str = Form(..., min_length=1, max_length=200),
    description: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Create a project with a ZIP file upload as source.

    The ZIP is extracted to data_dir/project-sources/{project_id}/
    and the project is created with source_type='zip'.
    """
    # Validate file
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(400, "Only .zip files are accepted")

    svc = ProjectService(db)
    from app.schemas.project import ProjectCreate
    req = ProjectCreate(name=name, description=description, source_type="zip",
                        source_config={"original_filename": file.filename})
    project = svc.create(req)

    # Extract ZIP to managed directory
    from app.core.config import settings
    data_dir = getattr(settings, 'data_dir', os.path.join(os.getcwd(), '.data'))
    extract_dir = os.path.join(data_dir, "project-sources", project.project_id)
    os.makedirs(extract_dir, exist_ok=True)

    try:
        contents = await file.read()
        import io
        with zipfile.ZipFile(io.BytesIO(contents)) as zf:
            # Security: guard against zip bombs and path traversal
            total_size = 0
            for member in zf.infolist():
                total_size += member.file_size
                if total_size > 100 * 1024 * 1024:  # 100MB limit
                    raise HTTPException(400, "ZIP too large (max 100MB uncompressed)")
                # Guard against path traversal in ZIP
                member_path = os.path.normpath(member.filename)
                if member_path.startswith('..') or os.path.isabs(member_path):
                    continue  # Skip dangerous paths
            zf.extractall(extract_dir)

        # Update source_config with extraction path
        svc.update(project.project_id, source_config={
            "original_filename": file.filename,
            "extracted_path": extract_dir,
            "file_count": len(zf.infolist()),
        })
    except HTTPException:
        raise
    except Exception as e:
        # Clean up on failure
        svc.delete(project.project_id)
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise HTTPException(400, f"Failed to process ZIP: {str(e)}")

    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="create_project_zip",
        summary=f"Created project {project.project_id} from ZIP: {file.filename}",
        project_id=project.project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )


# ── Git / ZIP / GitHub integration endpoints ──────────────────────────


@router.put("/{project_id}/source")
async def update_project_source(
    project_id: str,
    req: ProjectSourceUpdate,
    db: Session = Depends(get_db),
):
    """Update project source type and configuration (generic endpoint)."""
    svc = ProjectService(db)
    project = svc.update_source(project_id, req.source_type, req.source_config)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="update_source",
        summary=f"Source updated to {req.source_type} for project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )


@router.post("/{project_id}/integrations/git")
async def configure_git_source(
    project_id: str,
    git_host_id: str | None = None,
    remote_url: str | None = None,
    branch: str = "main",
    subpath: str = "/",
    db: Session = Depends(get_db),
):
    """Configure Git source for a project.

    Accepts either git_host_id (reference to a registered GitHost) or a
    direct remote_url.  Stores the configuration in the project's
    source_config column and sets source_type to 'git'.
    """
    svc = ProjectService(db)
    config = {
        "branch": branch,
        "subpath": subpath,
    }
    if git_host_id:
        config["git_host_id"] = git_host_id
    if remote_url:
        config["remote_url"] = remote_url

    project = svc.update_source(project_id, "git", config)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="source_git",
        summary=f"Git source configured for project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )


@router.post("/{project_id}/integrations/zip")
async def configure_zip_source(
    project_id: str,
    file_path: str | None = None,
    upload_ref: str | None = None,
    db: Session = Depends(get_db),
):
    """Configure ZIP source for a project.

    Accepts a file_path (local server path) or upload_ref (reference to a
    previously uploaded file).  Sets source_type to 'zip'.
    """
    svc = ProjectService(db)
    config = {}
    if file_path:
        config["file_path"] = file_path
    if upload_ref:
        config["upload_ref"] = upload_ref

    project = svc.update_source(project_id, "zip", config)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="source_zip",
        summary=f"ZIP source configured for project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )


@router.post("/{project_id}/integrations/github")
async def configure_github_source(
    project_id: str,
    repo_owner: str | None = None,
    repo_name: str | None = None,
    clone_url: str | None = None,
    branch: str = "main",
    subpath: str = "/",
    db: Session = Depends(get_db),
):
    """Configure GitHub source for a project.

    Accepts repo owner/name or a direct clone_url.  Sets source_type to 'github'.
    """
    svc = ProjectService(db)
    config = {
        "branch": branch,
        "subpath": subpath,
    }
    if repo_owner and repo_name:
        config["repo_owner"] = repo_owner
        config["repo_name"] = repo_name
    if clone_url:
        config["clone_url"] = clone_url

    project = svc.update_source(project_id, "github", config)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    svc_deps = get_services()
    svc_deps.trace_writer.write(
        "state_change", action="source_github",
        summary=f"GitHub source configured for project {project_id}",
        project_id=project_id,
    )
    return SuccessEnvelope(
        data=ProjectResponse(**svc.to_response(project)),
        meta=Meta(),
    )
