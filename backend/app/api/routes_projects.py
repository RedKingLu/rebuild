"""Project API routes — DB-backed CRUD with Trace audit."""

import logging
import os
import uuid
import zipfile
import shutil

logger = logging.getLogger("rebuild.routes_projects")
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
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
            meta=Meta(source_status="real"),
        ),
        meta=Meta(source_status="real"),  # R9-5-8 T11: real DB list, not "mock"
    )


@router.post("/precheck")
async def precheck_project(req: ProjectCreate, db: Session = Depends(get_db)):
    """R9-5-8 T10: pre-create validation (nothing persisted). Returns field-level
    checks; passed=False on a hard error (empty name / invalid type / Git without
    usable credential). The guide calls this before create — passed≠create success."""
    from app.services.precheck_service import run_precheck
    result = run_precheck(req.name, req.source_type, req.source_config,
                          services=get_services())
    return SuccessEnvelope(data=result.to_dict(), meta=Meta())


@router.post("")
async def create_project(
    req: ProjectCreate,
    db: Session = Depends(get_db),
):
    svc = ProjectService(db)
    svc_deps = get_services()

    # 1. Create project record (status = importing)
    project = svc.create(req)
    svc.update(project.project_id, workspace_status="importing")

    # 2. Initialize workspace directory structure (source/, materials/, artifacts/, ...)
    try:
        from app.services.workspace_service import init_workspace
        init_workspace(project.project_id)
    except Exception:
        logger.warning("Workspace init failed", exc_info=True)

    # 3. Source materialization — copy/clone source into workspace/source/
    src_type = project.source_type.value if hasattr(project.source_type, 'value') else str(project.source_type)
    materialized = {"materialization_status": "skipped", "file_count": 0}
    try:
        from app.services.source_materializer import SourceMaterializer, generate_source_index
        m = SourceMaterializer(trace_writer=svc_deps.trace_writer, audit_writer=svc_deps.audit_writer)
        materialized = m.materialize(project.project_id, src_type, project.source_config or {})
        if materialized.get("file_count", 0) > 0:
            generate_source_index(project.project_id)
    except Exception:
        logger.warning("Source materialization failed", exc_info=True)
        materialized = {"materialization_status": "error", "file_count": 0}

    # 4. Update project status based on materialization result (R9-5-8 T12/T8).
    #    Honest status — NEVER force "ready" over a failed/empty materialization.
    #      blocked  : materialization error/failed (user must act)
    #      deferred : non-manual source produced 0 files (creds / not yet imported)
    #      ready    : source present, or legitimately-empty manual project
    mat_status = materialized.get("materialization_status")
    file_count = materialized.get("file_count", 0)
    if mat_status in ("failed", "error"):
        final_status = "blocked"
    elif mat_status == "deferred" or (file_count == 0 and src_type not in ("manual",)):
        final_status = "deferred"
    else:
        final_status = "ready"
    svc.update(project.project_id, workspace_status=final_status)

    # 5. Environment Profile defaults
    try:
        from app.services.workspace_service import init_environment
        init_environment(project.project_id)
    except Exception:
        logger.warning("Environment init failed", exc_info=True)

    svc_deps.trace_writer.write(
        "state_change", action="create_project",
        summary=f"Created project {project.project_id}: {src_type}, "
                f"{materialized.get('file_count', 0)} files, status={materialized.get('materialization_status')}",
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
            # Security: guard against zip bombs and path traversal (R9-3F).
            # Extract member-by-member, skipping any path that escapes extract_dir,
            # so a malicious member can never be written outside the sandbox even
            # if stdlib sanitization changes.
            extract_root = os.path.realpath(extract_dir)
            total_size = 0
            extracted = 0
            for member in zf.infolist():
                total_size += member.file_size
                if total_size > 100 * 1024 * 1024:  # 100MB limit
                    raise HTTPException(400, "ZIP too large (max 100MB uncompressed)")
                member_path = os.path.normpath(member.filename)
                if member_path.startswith('..') or os.path.isabs(member_path):
                    continue  # Skip dangerous paths — do NOT extract
                dest = os.path.realpath(os.path.join(extract_dir, member_path))
                if dest != extract_root and not dest.startswith(extract_root + os.sep):
                    continue  # Zip-slip guard: target escapes sandbox
                zf.extract(member, extract_dir)
                extracted += 1

        # Update source_config with extraction path
        svc.update(project.project_id, source_config={
            "original_filename": file.filename,
            "extracted_path": extract_dir,
            "file_count": extracted,
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


# ── P0 Onboarding completion (R9-3B) ────────────────────────────────────

from pydantic import BaseModel as _BaseModel

class OnboardingCompleteRequest(_BaseModel):
    execution_mode: str = "plan"
    env_kind: str | None = None
    language_hint: str | None = None
    framework_hint: str | None = None
    coding_agent_ref: str | None = None
    # D-088 / R9-5-5: external platform delegation scope for this project
    external_platform_scope: str | None = None
    # D-098: model strategy chosen in the guide (global_unified → global_model_ref)
    model_strategy_mode: str | None = None
    global_model_ref: str | None = None
    # R9-5-7 T1/T2 (D-086②/D-094): submission method chosen in the guide.
    #   submission_kind ∈ {remote_git, local_git}. This round only remote_git is
    #   wired (本轮只接远端 Git); local_git is a read-only placeholder pending D-094.
    submission_kind: str | None = None
    git_remote_url: str | None = None
    git_branch: str | None = None


@router.post("/{project_id}/onboarding/complete")
async def complete_onboarding(project_id: str, req: OnboardingCompleteRequest, db: Session = Depends(get_db)):
    """Complete the P0 onboarding wizard and create the initial Run."""
    svc = ProjectService(db)
    svc_deps = get_services()
    project = svc.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # 1. Update Environment Profile
    try:
        from app.services.workspace_service import update_environment
        env_updates = {"status": "declared"}
        if req.env_kind:
            env_updates["env_kind"] = req.env_kind
        if req.language_hint:
            env_updates["language_hint"] = req.language_hint
        if req.framework_hint:
            env_updates["framework_hint"] = req.framework_hint
        update_environment(project_id, env_updates)
    except Exception:
        logger.warning("Environment update failed", exc_info=True)

    # 2. Update Project
    updates = {"onboarding_done": True}
    if req.coding_agent_ref:
        updates["coding_agent_ref"] = req.coding_agent_ref
    if req.external_platform_scope:
        updates["external_platform_scope"] = req.external_platform_scope
    # D-098: persist the model strategy chosen in the guide (引导可写, D-086)
    if req.model_strategy_mode:
        updates["model_strategy_mode"] = req.model_strategy_mode
    if req.global_model_ref is not None:
        updates["global_model_ref"] = req.global_model_ref
    # R9-5-7 T1/T2: persist remote-Git submission choice into source_config so the
    # materializer (step 5b) clones the chosen repo. Honest deferred if no creds.
    if req.submission_kind == "remote_git" and req.git_remote_url:
        from app.models.project import SourceType as _ST
        merged_cfg = dict(project.source_config or {})
        merged_cfg["remote_url"] = req.git_remote_url
        if req.git_branch:
            merged_cfg["branch"] = req.git_branch
        updates["source_type"] = _ST.git
        updates["source_config"] = merged_cfg
    svc.update(project_id, **updates)
    # Re-read so downstream materialization sees the updated source_type/config
    project = svc.get(project_id)

    # 3. Create P0 Run — WP-3: idempotent; return existing active Run if present
    run = None
    try:
        from app.services.run_service import RunService, _run_to_response as _r2r
        from app.schemas.run import RunCreate as _RunCreate
        from app.models.run import Run as _Run
        from app.core.database import get_session as _get_session
        # Check for existing p0 Run for this project (idempotency key: project_id + current_stage=p0 + active status)
        _idem_db = _get_session()
        _existing_run = None
        try:
            _existing_run = _idem_db.query(_Run).filter(
                _Run.project_id == project_id,
                _Run.current_stage == "p0",
                _Run.run_status.in_(["created", "running"]),
            ).first()
        finally:
            _idem_db.close()
        if _existing_run is not None:
            # Reuse existing Run — don't create duplicate
            run = _r2r(_existing_run)
            logger.info(f"WP-3 idempotent: reusing existing p0 Run {_existing_run.run_id} for project {project_id}")
        else:
            run_req = _RunCreate(run_goal=f"P0 接入：{project.name}", mode=req.execution_mode)
            run = RunService(svc_deps).create(project_id, run_req)
            svc.update(project_id, current_run_id=run.run_id, current_stage="p0")
            # R17-X 联调修复: complete 不再驱动 P0 图，故 P0 尚未真正开始执行。
            # RunService.create 默认写 stage_status.p0="in_progress"（那是"图已驱动"语义），
            # 这里回退为 "pending"，使状态诚实（已创建、未启动），前端据此显示一次性「欢迎页」；
            # 用户点「开始」→ POST /onboarding/execute 驱动图时再由 init_state 置 in_progress。
            try:
                RunService(svc_deps).set_stage_status(run.run_id, "p0", "pending")
            except Exception:
                logger.warning("R17-X: reset p0 stage_status to pending failed", exc_info=True)
    except Exception:
        logger.warning("Run creation failed", exc_info=True)

    # 3b. R9-5-7 T7: persist execution_mode to workspace.json — the SINGLE control
    #     source shared with the top-bar ExecModeSwitch (PUT /mode). Without this the
    #     wizard's mode choice was "选了不生效" (run.mode is only a per-Run snapshot).
    try:
        from app.services.workspace_service import set_execution_mode
        set_execution_mode(project_id, req.execution_mode)
    except Exception:
        logger.warning("execution_mode persist failed", exc_info=True)

    # 4. Write intake_report Artifact
    intake_id = f"artifact-intake-{project_id[:8]}"
    try:
        from app.services.workspace_service import workspace_path
        import json as _json
        art_dir = workspace_path(project_id) / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "intake_report.json").write_text(_json.dumps({
            "artifact_id": intake_id, "project_id": project_id, "stage": "p0",
            "artifact_type": "intake_report", "title": f"P0 接入报告：{project.name}",
            "source_type": str(project.source_type.value) if hasattr(project.source_type, 'value') else str(project.source_type),
            "execution_mode": req.execution_mode, "status": "generated",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("intake_report write failed", exc_info=True)

    # 5. Evidence candidates — WP-2: write real Evidence objects to workspace/evidence/
    #    so GET /evidence returns non-empty list; Gate evidence_refs point to real ids.
    _ev_ws_id = f"ev-p0-ws-{project_id[:8]}"
    _ev_onb_id = f"ev-p0-onb-{project_id[:8]}"
    evidence_candidates = [
        {"evidence_id": _ev_ws_id, "type": "workspace_initialized", "status": "candidate"},
        {"evidence_id": _ev_onb_id, "type": "onboarding_completed", "status": "candidate"},
    ]
    try:
        svc_deps.aet_service.write_evidence(
            project_id=project_id, evidence_id=_ev_ws_id,
            evidence_type="workspace_initialized", status="candidate",
            source="p0_onboarding", claim="P0 工作区已初始化", stage="p0",
        )
        svc_deps.aet_service.write_evidence(
            project_id=project_id, evidence_id=_ev_onb_id,
            evidence_type="onboarding_completed", status="candidate",
            source="p0_onboarding", claim="P0 Onboarding 已完成（Run 已建，产物已生成）", stage="p0",
        )
    except Exception:
        logger.warning("Evidence write failed", exc_info=True)

    # 5b. Source materialization is the P0 stage's real ACTION — it is performed by
    #     the LangGraph P0 node (RealP0Handler.execute) when the graph is driven below,
    #     NOT here. B-PLAN-1 (D-025): in manual/plan mode the graph pauses at a
    #     plan_review Gate BEFORE the P0 node executes, so materialization only runs
    #     after the user approves the接入计划（"审核通过后才执行动作"）. In auto mode the
    #     node materializes immediately during the graph drive. The route no longer
    #     pre-materializes (which used to run regardless of plan approval, and duplicated
    #     the node's work). `materialized` stays None here; the response reflects the
    #     Gate the graph resolves.
    materialized = None
    p0_artifact_refs = ["artifacts/intake_report.json"]

    # 6. Trace + Audit
    svc_deps.trace_writer.write("onboarding_complete", action="complete_onboarding",
        summary=f"Onboarding completed: {project.name} (P0 materialization deferred to graph node)",
        project_id=project_id)
    svc_deps.audit_writer.write(audit_type="onboarding_complete", action="complete_onboarding",
        decision="executed", risk_level="L2", project_id=project_id,
        reason=f"P0 onboarding: {project.name}")

    # 7. R17-X 联调修复（用户裁决，回退 T6b/W8 Approach A 的 "complete 自动驱动图"）:
    #    /onboarding/complete 不再自动驱动 P0 图，也不再创建 P0→P1 Gate。complete 只完成
    #    profile 保存 / Run 创建 / execution_mode 持久化(workspace.json 单一控制源) / intake /
    #    evidence 候选 / materialized(占位)。P0 图驱动完全交给用户在前端「欢迎页」点击「开始」
    #    触发的 POST /onboarding/execute（P0 规范主入口，B-R17X-DUALENTRY-2）。这样引导完成后
    #    平台不会自动执行 P0——先显示一次性「欢迎进入平台」界面，用户点「开始」后才正式启动 P0
    #    图执行；欢迎+启动为一次性（仅 P0 前一次），之后各阶段由 stage_promotion Gate 控制。
    #    因此 complete 返回 graph_driven=False / gate_id=None / review.passed=None（不假装已建 Gate）。
    _resp_data = {
        "project_id": project_id, "onboarding_done": True,
        "run_id": run.run_id if run else None,
        "intake_artifact_id": intake_id,
        "evidence_candidates": evidence_candidates,
        "materialized": materialized,
        # complete 不再审核（P0 审核由 execute 驱动的图节点产出），verdict 未知(None)
        "review": {"passed": None, "reviewer": "p0_review_skill",
                    "source": "langgraph_p0_node"},
        "p0_artifacts": p0_artifact_refs,
        # R17-X: complete 不自动驱动图、不创建 Gate — 交由 POST /onboarding/execute
        "gate_id": None,
        "gate_type": None,
        "checkpoint_ref": None,
        "graph_driven": False,
        "next": "点击『开始』(POST /onboarding/execute) 启动 P0 图执行",
    }
    return SuccessEnvelope(data=_resp_data, meta=Meta())


# ── P1 Full-Stack Profiling (R9-3C) ─────────────────────────────────────

class ProfilingRequest(_BaseModel):
    pass  # No params needed — profiling reads from workspace/source/



# ── P0 Agent-driven execution (WP-6: LangGraph delegate) ────────────────────

@router.post("/{project_id}/onboarding/execute")
async def execute_onboarding(project_id: str, db: Session = Depends(get_db)):
    """P0 canonical entry (规范主入口, B-R17X-DUALENTRY-2) — the thin SSE trigger that
    drives the LangGraph P0 node. This is the production P0 启动入口.

    Streams SSE events from the real graph execution (FlowRuntime.astream_events).
    Gate created by the graph carries real checkpoint_ref (thread_id=run_id).
    Falls back to the legacy inline pipeline only if FlowRuntime is unavailable.

    Call this AFTER /onboarding/complete.
    """
    import json as _json
    import asyncio as _asyncio

    svc = ProjectService(db)
    svc_deps = get_services()
    project = svc.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # WP-3 F-3 idempotency guard (before stream):
    # 1. If project already past p0, reject to prevent creating stale P0 Gates
    _stage_order = ["p0", "p1", "p2", "p3", "p4", "p5", "p6"]
    _cur_stage = (project.current_stage or "p0").lower()
    if _cur_stage in _stage_order and _stage_order.index(_cur_stage) > 0:
        raise HTTPException(
            409,
            f"项目当前阶段已是 {_cur_stage}，不能重新执行 P0 onboarding。"
            f"（当前阶段 > p0，拒绝重建 P0 Gate）",
        )
    # 2. If there is already an active P0 Gate, return it (don't create duplicate).
    #    T6b: /onboarding/complete now drives the graph and creates this gate, so
    #    the idempotent path is the common case — surface full gate fields
    #    (incl. source_pending retry_action) so clients get the same info as a drive.
    _active_gate_id = getattr(project, "active_gate", None)
    if _active_gate_id:
        _existing_gate = svc_deps.gate_service.get(_active_gate_id)
        if _existing_gate and _existing_gate.stage == "p0" and _existing_gate.gate_status == "waiting_decision":
            from app.graph.runtime import graph_capability_probe as _gcp
            _idem_payload = {
                "message": "P0 Gate 已存在，无需重复执行。",
                "gate_id": _active_gate_id,
                "gate_type": _existing_gate.gate_type,
                "checkpoint_ref": _existing_gate.checkpoint_ref,
                "run_id": _existing_gate.run_id,
                "graph_capability_status": _gcp(),
                "graph_driven": True,
                "idempotent": True,
            }
            if _existing_gate.gate_type == "source_pending":
                _idem_payload["retry_action"] = "update_source_config"
                if _existing_gate.reason:
                    _idem_payload["gate_reason"] = _existing_gate.reason
            _idem_data = _json.dumps(_idem_payload, ensure_ascii=False)
            async def _idem_stream():
                yield f"event: complete\ndata: {_idem_data}\n\n"
            return StreamingResponse(_idem_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── WP-6: delegate to FlowRuntime (LangGraph P0 node) ────────────────
    run_id = getattr(project, "current_run_id", None) or ""

    def _src_type_str():
        st = project.source_type
        return st.value if hasattr(st, "value") else str(st)

    async def graph_stream():
        from app.graph.runtime import get_flow_runtime, graph_capability_probe
        nonlocal run_id

        def _emit(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"

        # If no run exists yet, create one now (complete_onboarding may have been skipped)
        if not run_id:
            try:
                from app.services.run_service import RunService
                from app.schemas.run import RunCreate as _RC
                r = RunService(svc_deps).create(project_id, _RC(run_goal=f"P0 接入：{project.name}"))
                run_id = r.run_id
                svc.update(project_id, current_run_id=run_id, current_stage="p0")
            except Exception as exc:
                logger.warning("execute_onboarding: run creation failed: %s", exc, exc_info=True)
                yield _emit("error", {"message": f"Run 创建失败：{exc}"})
                return

        yield _emit("status", {"phase": "graph_start", "message": "正在启动 LangGraph P0 节点…", "run_id": run_id})
        await _asyncio.sleep(0.05)

        rt = get_flow_runtime()
        # R17-X D-联调-2: drive the graph with the user's REAL chosen execution_mode.
        # workspace.json is the single control source (set by /onboarding/complete via
        # set_execution_mode, and by the top-bar ExecModeSwitch). Previously this was a
        # hardcoded "plan" bug — auto/manual selections silently ran as plan. Fallback to
        # the Run's execution_mode snapshot, then "plan".
        _exec_mode = None
        try:
            from app.services.workspace_service import get_execution_mode as _get_mode
            _exec_mode = _get_mode(project_id)
        except Exception:
            _exec_mode = None
        if not _exec_mode:
            try:
                from app.services.run_service import RunService as _RS
                _run_now = _RS(svc_deps).get(run_id)
                _exec_mode = getattr(_run_now, "execution_mode", None) or "plan"
            except Exception:
                _exec_mode = "plan"
        init_state = {
            "run_id": run_id,
            "project_id": project_id,
            "run_goal": f"P0 接入：{project.name}",
            "run_status": "running",
            "source_type": _src_type_str(),
            "source_config": project.source_config or {},
            "execution_mode": _exec_mode,
            "stage_status": {"p0": "in_progress"},
            "current_stage": "p0",
        }

        gate_id = None
        gate_type = None
        checkpoint_ref = None
        final_stage = "p0"
        had_interrupt = False

        try:
            async for ev in rt.astream_events(run_id, init_state=init_state):
                etype = ev.get("event", "")
                # Surface graph events as SSE status events
                if etype == "on_chain_start":
                    node = ev.get("name", "")
                    if node:
                        yield _emit("status", {"phase": "node_start", "node": node,
                                               "message": f"节点 {node} 开始执行…"})
                elif etype == "on_chain_end":
                    node = ev.get("name", "")
                    if node:
                        yield _emit("status", {"phase": "node_end", "node": node,
                                               "message": f"节点 {node} 执行完成"})
                elif etype == "on_chain_stream":
                    pass  # skip intermediate stream chunks
                # Extract gate_id from interrupt events
                data = ev.get("data") or {}
                if isinstance(data, dict):
                    output = data.get("output") or {}
                    if isinstance(output, dict):
                        pg = output.get("pending_gate") or {}
                        if isinstance(pg, dict) and pg.get("gate_id"):
                            gate_id = pg["gate_id"]
                            checkpoint_ref = run_id  # thread_id == run_id

        except Exception as exc:
            # Check if it's an interrupt (normal pause at gate)
            exc_str = str(exc)
            if "interrupt" in exc_str.lower() or "GraphInterrupt" in type(exc).__name__:
                had_interrupt = True
            else:
                logger.warning("graph stream error: %s", exc, exc_info=True)
                yield _emit("error", {"message": f"图执行失败：{exc}"})
                return

        # After graph execution (which paused at interrupt), query the Gate
        # The gate was created by RealGateBackend with checkpoint_ref=run_id
        gate_reason = ""
        if not gate_id:
            # Look up active gate created during this graph run
            active_gate = svc_deps.gate_service.get_active(project_id)
            if active_gate and active_gate.run_id == run_id:
                gate_id = active_gate.gate_id
                gate_type = active_gate.gate_type
                gate_reason = active_gate.reason or ""
                checkpoint_ref = active_gate.checkpoint_ref or run_id
        else:
            # gate_id was extracted from graph events; pull full gate for type info
            try:
                _gt = svc_deps.gate_service.get(gate_id)
                if _gt:
                    gate_type = _gt.gate_type
                    gate_reason = _gt.reason or ""
            except Exception:
                # 发声：Gate 查询失败会让 gate_type/reason 静默缺省，掩盖 Gate 查询故障。
                logger.warning("resume: 查询 Gate 详情失败 gate=%s", gate_id, exc_info=True)

        # Update project state
        if gate_id:
            svc.update(project_id, active_gate=gate_id)

        # REC-R17.4-2 (WP-D): backfill the Run row's own run_status after the initial
        # P0 graph run. A run created with status="created" that then pauses at a Gate
        # must not stay stale — surface waiting_gate (Gate pending) / running honestly.
        try:
            svc_deps.run_service.set_run_status(
                run_id, "waiting_gate" if gate_id else "running")
        except Exception:
            logger.warning("P0 图执行后回填 run_status 失败 run=%s（advisory）", run_id, exc_info=True)

        # Re-read project to get real state
        proj_now = svc.get(project_id)
        final_stage = (proj_now.current_stage if proj_now else "p0") or "p0"

        # surface graph capability status
        cap_status = rt.capability_status()
        _complete_data = {
            "message": f"P0 接入完成（LangGraph 图节点执行）。Gate 已创建。",
            "gate_id": gate_id,
            "gate_type": gate_type or "stage_promotion",
            "checkpoint_ref": checkpoint_ref,
            "run_id": run_id,
            "current_stage": final_stage,
            "graph_capability_status": cap_status,
            "graph_driven": True,
        }
        # WP-1: surface retry_action for source_pending gates so clients can guide recovery
        if gate_type == "source_pending":
            _complete_data["retry_action"] = "update_source_config"
            if gate_reason:
                _complete_data["gate_reason"] = gate_reason
        yield _emit("complete", _complete_data)

    return StreamingResponse(graph_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{project_id}/profile")
async def run_profiling(project_id: str, req: ProfilingRequest | None = None, db: Session = Depends(get_db)):
    """Run P1 full-stack profiling on the project workspace.

    Generates all 14 identification artifacts, profiling summary,
    and P2 input manifest. Creates P1→P2 promotion Gate.
    """
    svc = ProjectService(db)
    svc_deps = get_services()
    project = svc.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # Ensure workspace source/ exists
    from app.services.workspace_service import workspace_path
    ws_source = workspace_path(project_id) / "source"
    if not ws_source.exists() or not any(ws_source.iterdir()):
        # Try materialization first
        try:
            from app.services.source_materializer import SourceMaterializer
            m = SourceMaterializer(trace_writer=svc_deps.trace_writer, audit_writer=svc_deps.audit_writer)
            src_type = project.source_type.value if hasattr(project.source_type, 'value') else str(project.source_type)
            m.materialize(project_id, src_type, project.source_config or {})
        except Exception:
            logger.warning("Profiling pre-materialization failed", exc_info=True)

    # Run the profiler THROUGH a Review Pass (R9-3F: execute→review→retry≤2→escalate)
    from app.services.full_stack_profiler import FullStackProfiler
    from app.services.review_pass import ReviewPass, ReviewResult
    from app.services.workspace_service import workspace_path as _wp
    profiler = FullStackProfiler(
        trace_writer=svc_deps.trace_writer,
        audit_writer=svc_deps.audit_writer,
    )

    def _review_profiling(prof_result: dict) -> ReviewResult:
        """Quality gate for P1 profiling output (real artifacts on disk)."""
        issues = []
        art = _wp(project_id) / "artifacts"
        if prof_result.get("items_completed", 0) < 1:
            issues.append({"type": "no_artifacts", "detail": "profiler produced no identification artifacts"})
        if not (art / "p2_input_manifest.json").exists():
            issues.append({"type": "missing_p2_manifest", "detail": "p2_input_manifest.json not generated"})
        if not (art / "profiling_summary.md").exists():
            issues.append({"type": "missing_summary", "detail": "profiling_summary.md not generated"})
        return ReviewResult(
            passed=not issues, issues=issues,
            recommendations=["重新执行全量识别并确认产物写入"] if issues else [],
            reviewer="profile_review_skill",
        )

    rp = ReviewPass(max_rounds=2, tracer=svc_deps.trace_writer, auditor=svc_deps.audit_writer)
    review_outcome = await rp.run(
        execute_fn=lambda: profiler.profile(project_id),
        review_fn=_review_profiling,
        project_id=project_id, stage="p1",
    )
    result = review_outcome.get("final_result") or {"items_completed": 0, "gaps_count": 0}

    # If Review Pass escalated (failed after max rounds), do NOT create the
    # promotion Gate — surface the escalation honestly.
    if not review_outcome.get("passed"):
        svc_deps.trace_writer.write("profiling_review", action="run_profiling",
            summary=f"P1 profiling escalated after review: {review_outcome.get('escalation_reason')}",
            project_id=project_id)
        return SuccessEnvelope(data={
            "project_id": project_id,
            "profiling_result": result,
            "review": {"passed": False, "rounds": review_outcome.get("rounds"),
                       "escalated_to_gate": review_outcome.get("escalated_to_gate"),
                       "escalation_reason": review_outcome.get("escalation_reason")},
            "gate_id": None,
            "next_stage": None,
        }, meta=Meta())

    # B-PLAN-1 / B-P0-FAKE-1 (R11-3): produce the REAL three D-092 reports via
    # StageReports (the same framework the graph P1 node uses) from the real profiler
    # result + the real ReviewPass verdict. This replaces the old template / hardcoded
    # (14-item) / always-pass p1_stage_plan / p1_execution_record / p1_construction_report
    # / p1_review_pass files, which were fabrications forbidden by D-101. Gate refs =
    # the real reports + the real profiler artifacts on disk.
    from app.graph.stage_reports import StageReports
    from app.graph.stage_handlers import RealP1Handler as _P1H
    _reports = StageReports(project_id, "p1")
    _reports.start_plan(goal=_P1H.goal, acceptance_criteria=list(_P1H.acceptance_criteria),
                        planned_actions=list(_P1H.planned_actions))
    _art = _wp(project_id) / "artifacts"
    _produced = [f"artifacts/{p.name}" for p in sorted(_art.glob("*.json"))]
    if (_art / "profiling_summary.md").exists():
        _produced.append("artifacts/profiling_summary.md")
    _reports.construction(rounds=review_outcome.get("rounds", []) or [],
                          actions=list(_P1H.planned_actions),
                          produced_artifacts=_produced)
    _reports.acceptance(passed=bool(review_outcome.get("passed")),
                        issues=[str(i) for i in review_outcome.get("issues", [])],
                        recommendations=[str(r) for r in review_outcome.get("recommendations", [])],
                        reviewer="profile_review_skill")
    p1_artifact_refs = _reports.all_refs() + _produced

    # Create P1→P2 Gate
    gate_id = None
    try:
        gate = svc_deps.gate_service.create(
            project_id=project_id,
            run_id=project.current_run_id,
            stage="p1",
            gate_type="stage_promotion",
            reason="P1 全量识别完成，请求进入 P2 评估阶段",
            summary=f"全量识别完成：{result['items_completed']} 项产出，{result['gaps_count']} 个待确认项。请点击横幅查看审核材料后做出决策。",
            options=["approve", "reject", "request_changes"],
            artifact_refs=p1_artifact_refs,
        )
        gate_id = gate.gate_id if hasattr(gate, 'gate_id') else gate.get("gate_id", "")
        svc.update(project_id, active_gate=gate_id, current_stage="p1")
    except Exception:
        logger.warning("P1 Gate creation failed", exc_info=True)

    # Trace + Audit
    svc_deps.trace_writer.write("profiling_complete", action="run_profiling",
        summary=f"P1 profiling: {result['items_completed']} items, {result['gaps_count']} gaps",
        project_id=project_id)
    svc_deps.audit_writer.write(audit_type="profiling_complete", action="run_profiling",
        decision="executed", risk_level="L2", project_id=project_id,
        reason=f"P1 profiling: {result['items_completed']} artifacts")

    return SuccessEnvelope(data={
        "project_id": project_id,
        "profiling_result": result,
        "review": {"passed": True, "rounds": review_outcome.get("rounds"), "reviewer": "profile_review_skill"},
        "gate_id": gate_id,
        "next_stage": "p2",
    }, meta=Meta())
