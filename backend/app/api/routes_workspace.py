"""Workspace API routes — aggregate + file read/write + material tree + execution.

R8: Adds real file system scanning, file read/write with boundary protection,
material tree, and terminal execution endpoint.
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta
from app.services.workspace_service import (
    scan_file_tree, scan_material_tree, read_file, write_file,
    workspace_path, init_workspace,
    read_environment, update_environment,
)
from app.services.execution_provider import get_execution_provider

logger = logging.getLogger("rebuild.routes_workspace")

router = APIRouter(prefix="/projects/{project_id}", tags=["workspace"])


# ── Request schemas ──────────────────────────────────────────────────────

class FileWriteRequest(BaseModel):
    path: str
    content: str


class TerminalExecuteRequest(BaseModel):
    command: str
    language: str = "shell"
    timeout: int = 30
    session_id: str | None = None
    confirm: bool = False  # R9-3F: user confirmation for manual/plan-gated actions


class EnvironmentUpdateRequest(BaseModel):
    status: str | None = None
    env_kind: str | None = None
    language_hint: str | None = None
    framework_hint: str | None = None
    source: str | None = None


# ── Aggregate ────────────────────────────────────────────────────────────

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


# ── Workspace init (for existing projects without workspace) ─────────────

@router.post("/workspace/init")
async def init_project_workspace(project_id: str):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        ws = init_workspace(project_id)
        svc.project_service.update(project_id, workspace_status="initialized")
        svc.trace_writer.write("workspace_action", action="init_workspace",
                               summary=f"Initialized workspace for {project_id}", project_id=project_id)
        return SuccessEnvelope(data={"workspace_path": str(ws), "status": "initialized"}, meta=Meta())
    except Exception as e:
        raise HTTPException(500, f"Workspace init failed: {e}")


# ── Environment Profile (D-051, R8) ───────────────────────────────────────

@router.get("/environment")
async def get_environment(project_id: str):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    profile = read_environment(project_id)
    return SuccessEnvelope(data=profile, meta=Meta(source_status="real", capability_status="available"))


@router.put("/environment")
async def put_environment(project_id: str, req: EnvironmentUpdateRequest):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        profile = update_environment(project_id, updates)
    except ValueError as e:
        raise HTTPException(400, str(e))
    svc.trace_writer.write("workspace_action", action="update_environment",
                           summary=f"Environment profile updated for {project_id}",
                           project_id=project_id, extras={"fields": list(updates.keys())})
    svc.audit_writer.write(audit_type="environment_update", action="update_environment",
                           decision="executed", risk_level="L1",
                           project_id=project_id, reason=f"Updated env profile fields: {list(updates.keys())}")
    return SuccessEnvelope(data=profile, meta=Meta(source_status="real", capability_status="available"))


# ── Environment binding (R14-4) ─────────────────────────────────────────
# Workspace ↔ RemoteHost binding, default-environment selection, invocation
# history and read-only environment detection (G8).

class BindingCreateRequest(BaseModel):
    remote_host_id: str
    is_default: bool = False
    remote_workdir: str | None = None


class BindingSetDefaultRequest(BaseModel):
    binding_id: str | None = None


def _sync_db_default(db, workspace_id: str, default_binding_id: str | None) -> None:
    """Keep DB WorkspaceEnvironmentBinding.is_default in sync with workspace.json's
    authoritative default_binding_id (R14-6, B-R14-MULTIBIND-1).

    Clears every default row for the workspace first, flushes, then marks the one
    default — so the partial unique index (WHERE is_default = 1) is never
    transiently violated within the transaction.
    """
    from app.models.workspace_environment_binding import WorkspaceEnvironmentBinding
    rows = (db.query(WorkspaceEnvironmentBinding)
            .filter_by(workspace_id=workspace_id).all())
    for r in rows:
        if r.is_default:
            r.is_default = False
    db.flush()
    if default_binding_id:
        b = db.get(WorkspaceEnvironmentBinding, default_binding_id)
        if b is not None and b.workspace_id == workspace_id:
            b.is_default = True
    db.commit()


@router.get("/environment/block")
async def get_env_block(project_id: str):
    """Read the workspace environment block (single source of truth)."""
    from app.services.workspace_service import get_environment_block
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    env = get_environment_block(project_id)
    return SuccessEnvelope(data=env, meta=Meta(source_status="real", capability_status="available"))


class BindingAddResponse(BaseModel):
    env: dict


@router.post("/environment/bindings")
async def post_binding(project_id: str, req: BindingCreateRequest):
    """Bind a remote host to this workspace. Optionally set as default."""
    from app.core.database import get_session
    from app.services.workspace_service import add_binding_to_workspace
    from app.models.workspace_environment_binding import WorkspaceEnvironmentBinding
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    # Validate remote_host exists.
    with get_session() as db:
        host = db.get(__import__("app.models", fromlist=["RemoteHost"]).RemoteHost, req.remote_host_id)
        if host is None:
            raise HTTPException(404, f"RemoteHost {req.remote_host_id} not found")
        existing = db.query(WorkspaceEnvironmentBinding).filter_by(
            workspace_id=project_id, remote_host_id=req.remote_host_id).first()
        binding_id = existing.binding_id if existing else None
        if existing is None:
            b = WorkspaceEnvironmentBinding(
                workspace_id=project_id,
                remote_host_id=req.remote_host_id,
                remote_workdir=req.remote_workdir or f"/workspace/{project_id}",
            )
            db.add(b)
            db.commit()
            db.refresh(b)
            binding_id = b.binding_id
    env = add_binding_to_workspace(project_id, binding_id, set_default=req.is_default)
    # R14-6: mirror workspace.json's authoritative default onto the DB is_default
    # column so it is a live, index-enforced value (not a dead column).
    with get_session() as db:
        _sync_db_default(db, project_id, env.get("default_binding_id"))
    svc.trace_writer.write("workspace_action", action="add_binding",
                           summary=f"Binding {binding_id} → workspace {project_id}",
                           project_id=project_id, extras={"binding_id": binding_id})
    return SuccessEnvelope(data=env, meta=Meta(source_status="real", capability_status="available"))


@router.post("/environment/default")
async def post_default_binding(project_id: str, req: BindingSetDefaultRequest):
    """Set (or clear) the default binding for this workspace."""
    from app.services.workspace_service import set_default_binding
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    env = set_default_binding(project_id, req.binding_id)
    from app.core.database import get_session
    with get_session() as db:
        _sync_db_default(db, project_id, env.get("default_binding_id"))
    return SuccessEnvelope(data=env, meta=Meta(source_status="real", capability_status="available"))


@router.delete("/environment/bindings/{binding_id}")
async def delete_binding(project_id: str, binding_id: str):
    """Remove a binding from the workspace block (soft: keep row, drop ref)."""
    from app.core.database import get_session
    from app.models.workspace_environment_binding import WorkspaceEnvironmentBinding
    from app.services.workspace_service import remove_binding_from_workspace
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    with get_session() as db:
        b = db.get(WorkspaceEnvironmentBinding, binding_id)
        if b is not None:
            b.status = __import__("app.models", fromlist=["BindingStatus"]).BindingStatus.disabled
            db.commit()
    env = remove_binding_from_workspace(project_id, binding_id)
    return SuccessEnvelope(data=env, meta=Meta(source_status="real", capability_status="available"))


@router.get("/environment/invocations")
async def get_invocations(project_id: str, limit: int = 20):
    """Recent remote invocations for this workspace (history panel)."""
    from app.core.database import get_session
    from app.models.remote_invocation import RemoteInvocation
    from sqlalchemy import desc
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    with get_session() as db:
        rows = (db.query(RemoteInvocation)
                .filter_by(workspace_id=project_id)
                .order_by(desc(RemoteInvocation.started_at))
                .limit(limit).all())
        items = [{
            "invocation_id": r.invocation_id,
            "provider": r.provider.value,
            "trigger": r.trigger.value,
            "command_digest": r.command_digest,
            "exit_code": r.exit_code,
            "risk_level": r.risk_level,
            "elapsed_ms": r.elapsed_ms,
            "status": r.status,
            "stdout_ref": r.stdout_ref,
            "stderr_ref": r.stderr_ref,
            "artifact_ref": r.artifact_ref,
            "trace_ref": r.trace_ref,
            "audit_ref": r.audit_ref,
            "started_at": r.started_at.isoformat() if r.started_at else None,
        } for r in rows]
    return SuccessEnvelope(data={"items": items}, meta=Meta(source_status="real", capability_status="available"))


@router.get("/environment/detect/{remote_host_id}")
async def get_detect_environment(project_id: str, remote_host_id: str):
    """G8: read-only OS/runtime/service fingerprint of a remote host."""
    from app.core.database import get_session
    from app.models.remote_host import RemoteHost
    from app.services.execution_provider import get_execution_provider
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    with get_session() as db:
        host = db.get(RemoteHost, remote_host_id)
        if host is None:
            raise HTTPException(404, f"RemoteHost {remote_host_id} not found")
        provider = get_execution_provider(mode="remote", remote_host_id=remote_host_id, db=db)
        result = await provider.detect_environment()
    return SuccessEnvelope(data=result, meta=Meta(
        source_status="real",
        capability_status="available" if result.get("ok") else "error",
    ))


# ── File tree ─────────────────────────────────────────────────────────────

@router.get("/files")
async def get_file_tree(project_id: str):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        tree = scan_file_tree(project_id)
    except Exception as e:
        raise HTTPException(500, f"File tree scan failed: {e}")
    return SuccessEnvelope(data={"roots": [t.model_dump() for t in tree]}, meta=Meta())


# ── Material tree ─────────────────────────────────────────────────────────

@router.get("/materials")
async def get_material_tree(project_id: str):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        tree = scan_material_tree(project_id)
    except Exception as e:
        raise HTTPException(500, f"Material tree scan failed: {e}")
    return SuccessEnvelope(data={"roots": [t.model_dump() for t in tree]}, meta=Meta())


# ── File read ─────────────────────────────────────────────────────────────

@router.get("/file")
async def get_file_content(project_id: str, path: str = Query(..., description="Relative file path")):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        result = read_file(project_id, path)
    except PermissionError as e:
        svc.trace_writer.write("security_violation", action="read_file_blocked",
                               summary=str(e), project_id=project_id, extras={"path": path})
        raise HTTPException(403, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except IsADirectoryError as e:
        raise HTTPException(400, str(e))
    svc.trace_writer.write("file_action", action="read_file",
                           summary=f"Read {path}", project_id=project_id, extras={"path": path})
    return SuccessEnvelope(data=result, meta=Meta())


# ── File write ────────────────────────────────────────────────────────────

@router.put("/file")
async def put_file_content(project_id: str, req: FileWriteRequest):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        result = write_file(project_id, req.path, req.content)
    except PermissionError as e:
        svc.audit_writer.write(audit_type="file_write_denied", action="write_file_blocked",
                               decision="deny", risk_level="L2",
                               project_id=project_id, reason=str(e), extras={"path": req.path})
        raise HTTPException(403, str(e))
    # Write Trace + Audit for successful save (R8 requirement)
    svc.trace_writer.write("file_action", action="write_file",
                           summary=f"Saved {req.path} ({result['bytes']} bytes)", project_id=project_id,
                           extras=result)
    svc.audit_writer.write(audit_type="file_write", action="write_file",
                           decision="executed", risk_level="L2",
                           project_id=project_id, reason=f"User saved {req.path}",
                           extras={"path": req.path, "bytes": result["bytes"]})
    return SuccessEnvelope(data=result, meta=Meta())


# ── Terminal / execution ──────────────────────────────────────────────────

@router.post("/execute")
async def execute_command(project_id: str, req: TerminalExecuteRequest):
    """Execute a command via ExecutionProvider. Writes Trace/Audit.

    Accepts language='shell' for terminal commands or 'python' for code execution.
    Low-risk commands execute directly; high-risk commands are intercepted.
    """
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # Security: deny-list check (D-076: deny-list owned by ExecutionProvider)
    from app.services.execution_provider import DENY_SUBSTRINGS
    cmd_lower = req.command.lower().strip()

    # Check danger patterns first
    blocked_reason = None
    for pattern in DENY_SUBSTRINGS:
        if pattern.lower() in cmd_lower:
            blocked_reason = f"命令包含禁止片段 {pattern!r}"
            break

    if blocked_reason:
        svc.audit_writer.write(audit_type="command_blocked", action="terminal_execute",
                               decision="deny", risk_level="L4",
                               project_id=project_id, reason=blocked_reason,
                               extras={"command": req.command})
        return SuccessEnvelope(data={
            "exit_code": -1,
            "stdout": "",
            "stderr": blocked_reason,
            "blocked": True,
            "elapsed_ms": 0,
            "provider": "blocked",
            "execution_mode": "blocked",
        }, meta=Meta())

    # R9-3F: mode-aware authorization — real behavioral difference + Auto proxy.
    from app.services.mode_policy import authorize_action
    from app.services.workspace_service import get_execution_mode
    current_mode = get_execution_mode(project_id)  # single source (R9-5-7)
    # Ordinary terminal command = L2 (dangerous commands are deny-listed as L4 above).
    action_risk = "L2"
    authz = authorize_action(current_mode, action_risk, req.command[:80], confirmed=req.confirm)
    svc.trace_writer.write("authorization", action="authorize_execute",
        summary=f"Auth[{current_mode}]={authz['decision']} for: {req.command[:60]}",
        project_id=project_id,
        extras={"decision": authz["decision"], "mode": current_mode, "risk_level": action_risk})
    if authz["decision"] == "require_confirmation":
        a = svc.audit_writer.write(audit_type="authorization", action="authorize_execute",
            decision="require_confirmation", risk_level=action_risk, project_id=project_id,
            reason=authz["reason"], extras={"mode": current_mode, "command": req.command[:80]})
        authz["audit_ref"] = a.get("audit_id") if isinstance(a, dict) else getattr(a, "audit_id", None)
        return SuccessEnvelope(data={
            "requires_confirmation": True, "executed": False, "blocked": False,
            "authorization": authz, "exit_code": None, "stdout": "", "stderr": "",
            "execution_mode": current_mode,
        }, meta=Meta())
    if authz["decision"] == "auto_approved":
        a = svc.audit_writer.write(audit_type="authorization", action="auto_authorize",
            decision="auto_approved", risk_level=action_risk, project_id=project_id,
            reason=authz["reason"], extras={"mode": current_mode, "command": req.command[:80]})
        authz["audit_ref"] = a.get("audit_id") if isinstance(a, dict) else getattr(a, "audit_id", None)

    # Create or reuse execution session
    session_id = req.session_id
    if not session_id:
        session_id = f"es-{uuid.uuid4().hex[:8]}"
        # Write initial session record
        session_dir = workspace_path(project_id) / ".rebuild" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        import json
        from datetime import datetime, timezone
        (session_dir / f"{session_id}.json").write_text(json.dumps({
            "session_id": session_id,
            "project_id": project_id,
            "provider": os.environ.get("EXECUTION_MODE", "local"),
            "status": "running",
            "command": req.command,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # Execute via ExecutionProvider
    provider = get_execution_provider()
    try:
        result = await provider.execute(
            code=req.command,
            language=req.language if req.language == "python" else "shell",
            timeout=req.timeout,
            cwd=str(workspace_path(project_id)),
        )
    except Exception as e:
        result = {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution error: {e}",
            "elapsed_ms": 0,
            "provider": provider.name,
            "blocked": False,
            "execution_mode": os.environ.get("EXECUTION_MODE", "local"),
        }

    # Write Trace
    svc.trace_writer.write("terminal_execute", action="execute",
                           summary=f"Executed: {req.command[:80]} (exit={result.get('exit_code')})",
                           project_id=project_id,
                           extras={"session_id": session_id, "command": req.command,
                                   "exit_code": result.get("exit_code")})

    # Write Audit for L3+ commands
    risk = "L4" if req.language == "shell" else "L3"
    svc.audit_writer.write(audit_type="terminal_execute", action="execute",
                           decision="executed", risk_level=risk,
                           project_id=project_id,
                           reason=f"Terminal execute: {req.command[:80]}",
                           extras={"session_id": session_id, "exit_code": result.get("exit_code")})

    # Update session record
    try:
        session_file = workspace_path(project_id) / ".rebuild" / "sessions" / f"{session_id}.json"
        if session_file.exists():
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            sdata = _json.loads(session_file.read_text(encoding="utf-8"))
            sdata["status"] = "succeeded" if result.get("exit_code") == 0 else "failed"
            sdata["exit_code"] = result.get("exit_code")
            sdata["stdout_summary"] = (result.get("stdout") or "")[:500]
            sdata["stderr_summary"] = (result.get("stderr") or "")[:500]
            sdata["elapsed_ms"] = result.get("elapsed_ms", 0)
            sdata["ended_at"] = _dt.now(_tz.utc).isoformat()
            session_file.write_text(_json.dumps(sdata, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # 发声：执行会话记录持久化失败会让该次运行在会话历史中"消失"（看似未执行）。
        logger.warning("持久化执行会话记录失败 project=%s session=%s",
                       project_id, session_id, exc_info=True)

    return SuccessEnvelope(data={
        "session_id": session_id,
        "authorization": authz,
        **result,
    }, meta=Meta())


# ── Session list ──────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions(project_id: str):
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    sessions = []
    session_dir = workspace_path(project_id) / ".rebuild" / "sessions"
    if session_dir.exists():
        import json as _json
        for f in sorted(session_dir.glob("es-*.json"), reverse=True):
            try:
                sessions.append(_json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                # 发声：会话记录文件损坏被静默丢弃会让该会话从列表中"消失"，掩盖数据损坏。
                logger.warning("读取执行会话记录失败，已跳过 file=%s", f, exc_info=True)
    return SuccessEnvelope(data={"sessions": sessions[:50]}, meta=Meta())


# ── Execution mode switching (R9-3A, D-081) ──────────────────────────────

class ModeSwitchRequest(BaseModel):
    mode: str = Field(..., pattern="^(manual|plan|auto)$")


@router.get("/mode")
async def get_mode(project_id: str):
    """Read current execution mode for the workspace (single source: workspace.json)."""
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    from app.services.workspace_service import get_execution_mode
    current_mode = get_execution_mode(project_id)
    return SuccessEnvelope(data={"project_id": project_id, "execution_mode": current_mode}, meta=Meta())


@router.put("/mode")
async def switch_mode(project_id: str, req: ModeSwitchRequest):
    """Switch execution mode. Writes Trace (always) and Audit (if density changes)."""
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # Read previous mode (single source: workspace.json execution_mode)
    from app.services.workspace_service import get_execution_mode, set_execution_mode
    previous_mode = get_execution_mode(project_id)

    # Write through the shared single-source helper (R9-5-7 T6/T7, 公理6)
    new_mode = set_execution_mode(project_id, req.mode)

    # Trace: mode_change event
    svc.trace_writer.write(
        "mode_change", action="switch_mode",
        summary=f"Execution mode changed: {previous_mode} → {new_mode}",
        project_id=project_id,
        extras={"from_mode": previous_mode, "to_mode": new_mode},
    )

    # Audit if authorization density changes significantly
    if previous_mode != new_mode:
        svc.audit_writer.write(
            audit_type="mode_change", action="switch_mode",
            decision="executed", risk_level="L2",
            project_id=project_id,
            reason=f"User switched execution mode: {previous_mode} → {new_mode}",
            extras={"from_mode": previous_mode, "to_mode": new_mode},
        )

    return SuccessEnvelope(data={
        "project_id": project_id,
        "execution_mode": new_mode,
        "previous_mode": previous_mode,
    }, meta=Meta())


# ── Source materialization trigger (R9-3A, WP-A3) ────────────────────────

class MaterializeRequest(BaseModel):
    source_type: str | None = None


@router.post("/materialize")
async def trigger_materialization(project_id: str, req: MaterializeRequest | None = None):
    """Trigger source materialization for a project. Uses Project.source_config."""
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    from app.services.source_materializer import SourceMaterializer
    materializer = SourceMaterializer(
        trace_writer=svc.trace_writer,
        audit_writer=svc.audit_writer,
    )

    source_type = req.source_type if req and req.source_type else (
        project.source_type.value if hasattr(project.source_type, 'value')
        else str(project.source_type)
    )
    source_config = project.source_config or {}

    result = materializer.materialize(project_id, source_type, source_config)

    svc.trace_writer.write(
        "workspace_action", action="trigger_materialization",
        summary=f"Materialization {result['materialization_status']}: "
                f"{result['file_count']} files, {len(result['errors'])} errors",
        project_id=project_id,
        extras={"source_type": source_type, "status": result["materialization_status"]},
    )

    return SuccessEnvelope(data=result, meta=Meta())


# ── source_index endpoint (R9-3A, WP-A4) ─────────────────────────────────

@router.get("/source-index")
async def get_source_index(project_id: str):
    """Get or generate the source_index for a project."""
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    from app.services.source_materializer import generate_source_index
    index = generate_source_index(project_id)
    return SuccessEnvelope(data=index, meta=Meta())


def _now_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Context Assembly (R9-3D) ─────────────────────────────────────────────

@router.get("/context")
async def get_context(project_id: str, stage: str | None = None):
    """Assemble Context Package for a given stage (defaults to project.current_stage).

    R9-3D: Filters skills by category=common + category=<stage>.
    Other stage skills are excluded from the context.
    """
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    current_stage = stage or project.current_stage or "p0"
    from app.services.context_assembler import assemble_context
    ctx = assemble_context(
        project_id=project_id,
        current_stage=current_stage,
        project={
            "name": project.name,
            "source_type": project.source_type.value if hasattr(project.source_type, 'value') else str(project.source_type),
            "workspace_status": project.workspace_status,
            "onboarding_done": project.onboarding_done,
            "coding_agent_ref": project.coding_agent_ref,
        },
        run={"run_id": project.current_run_id} if project.current_run_id else None,
        include_skills=True,
    )
    svc.trace_writer.write("context_assembly", action="get_context",
        summary=f"Context assembled for stage={current_stage}, {len(ctx.get('skills', []))} skills",
        project_id=project_id, extras={"stage": current_stage})
    return SuccessEnvelope(data=ctx, meta=Meta())


# ── P1 Profiling Summary (R9-3D) ─────────────────────────────────────────
# B-P0-FAKE-1 (R11-3): P0 core artifacts are the REAL StageReports produced by the
# LangGraph P0 node (StageLoop → StageReports) + the real intake_report. The old
# p0_execution_record / p0_construction_report / p0_review_pass files were route-side
# fabrications (template / hardcoded / always-pass) and are no longer produced (D-101).
_P0_CORE_ARTIFACTS = [
    ("intake_report.json", "接入报告"),
    ("p0_start_plan.json", "起始计划报告"),
    ("p0_construction.json", "施工报告"),
    ("p0_acceptance.json", "验收报告"),
]


@router.get("/stage-artifacts/{stage}")
async def get_stage_artifacts(project_id: str, stage: str):
    """Return a stage's expected core artifacts with REAL existence (R9-5-8 T4).

    Replaces the frontend's hardcoded filename list that never checked existence.
    P0 returns its 4 core artifacts; other stages return what is actually present
    in artifacts/ (honest — a not-yet-produced artifact reads exists=false)."""
    svc = get_services()
    if svc.project_service.get(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    art_dir = workspace_path(project_id) / "artifacts"
    stage_l = (stage or "").lower()
    if stage_l == "p0":
        items = [{"name": name, "label": label, "exists": (art_dir / name).exists()}
                 for name, label in _P0_CORE_ARTIFACTS]
    else:
        present = sorted(f.name for f in art_dir.glob("*")) if art_dir.exists() else []
        items = [{"name": n, "label": n, "exists": True} for n in present]
    return SuccessEnvelope(data={"stage": stage_l, "artifacts": items}, meta=Meta())


@router.get("/profiling-summary")
async def get_profiling_summary(project_id: str):
    """Get the P1 profiling summary (Markdown) + the authoritative identification
    item list (R9-5-8 T2/T3). `items` is the single-source list (PROFILING_ITEMS)
    with per-item existence computed from real artifacts/{key}.json; `uncertainty`
    is the parsed uncertainty_manifest.json content (backs the Evidence Gaps card).
    The frontend renders from these — no hardcoded item list."""
    import json as _json
    from app.services.full_stack_profiler import PROFILING_ITEMS

    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    art_dir = workspace_path(project_id) / "artifacts"
    summary_path = art_dir / "profiling_summary.md"

    # Authoritative items + real existence (single source, T2)
    items = [
        {"key": key, "label": label,
         "exists": (art_dir / f"{key}.json").exists()}
        for key, label in PROFILING_ITEMS
    ]
    # Evidence Gaps source = uncertainty_manifest.json content (T3, not inferred)
    uncertainty = None
    um_path = art_dir / "uncertainty_manifest.json"
    if um_path.exists():
        try:
            uncertainty = _json.loads(um_path.read_text(encoding="utf-8"))
        except Exception:
            uncertainty = None

    if not summary_path.exists():
        return SuccessEnvelope(data={
            "available": False, "summary": "P1 全量识别尚未执行",
            "items": items, "uncertainty": uncertainty,
        }, meta=Meta())
    return SuccessEnvelope(data={
        "available": True,
        "summary": summary_path.read_text(encoding="utf-8"),
        "artifacts": [f.name for f in art_dir.glob("*.json")],
        "items": items,
        "uncertainty": uncertainty,
    }, meta=Meta())


# ── P2 Assessment Summary (R10 T18) ──────────────────────────────────────
# Read-only view of the 6 P2 assessment outputs written by RealP2Handler to
# workspace/artifacts/p2_*.json (契约 §4.5). Mirrors profiling-summary: parses the
# already-produced artifact files (no LLM call here) so StagePageP2 renders real
# content — risk_list/blocker_list/validation_gap_list/resource_needs items plus
# the assessment_report + embedded uncertainty_list. Honest empty state when P2 has
# not been assessed yet (available=false, not 404). model_used is a model identifier
# (NOT a Key/Token, §12.1) read best-effort from the ev-p2-model-analysis Evidence.
@router.get("/assessment-summary")
async def get_assessment_summary(project_id: str):
    import json as _json
    svc = get_services()
    if svc.project_service.get(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    art_dir = workspace_path(project_id) / "artifacts"

    def _read(name: str) -> dict | None:
        fp = art_dir / name
        if not fp.exists():
            return None
        try:
            return _json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            # honest: an unreadable/corrupt artifact is surfaced as missing content,
            # not silently treated as empty success (公理3)
            return {"_unreadable": True}

    report = _read("p2_assessment_report.json")
    risk = _read("p2_risk_list.json")
    blocker = _read("p2_blocker_list.json")
    gaps = _read("p2_validation_gaps.json")
    resources = _read("p2_resource_needs.json")

    # P2 not assessed yet → honest empty state (§4.10; no fabricated content)
    if report is None and risk is None and blocker is None:
        return SuccessEnvelope(data={
            "available": False,
            "reason": "P2 评估尚未执行。请先完成 P1 并批准 Gate 以触发 P2 评估。",
        }, meta=Meta())

    # best-effort model identifier (analysis-only marker) from persisted Evidence
    model_used = None
    try:
        ev = svc.aet_service.get_evidence("ev-p2-model-analysis", project_id=project_id)
        if isinstance(ev, dict):
            model_used = ev.get("model")
    except Exception:
        model_used = None

    return SuccessEnvelope(data={
        "available": True,
        # §4.7-5 / STOP-4: model output is auxiliary analysis, never fact
        "analysis_only": (report or {}).get("analysis_only", True),
        "model_used": model_used,
        "assessment_report": (report or {}).get("report", {}),
        "uncertainty_list": (report or {}).get("uncertainty_list", []),
        "risk_list": (risk or {}).get("items", []),
        "blocker_list": (blocker or {}).get("items", []),
        "validation_gap_list": (gaps or {}).get("items", []),
        "resource_needs": (resources or {}).get("items", []),
        "artifacts": [f"artifacts/{n}" for n in (
            "p2_assessment_report.json", "p2_risk_list.json", "p2_blocker_list.json",
            "p2_validation_gaps.json", "p2_resource_needs.json")
            if (art_dir / n).exists()],
    }, meta=Meta())


# ── P3 Planning Summary (R10 T19) ────────────────────────────────────────
# Read-only view of the P3 plan (Stage Plan + Task Plan Batch + TaskGraph) for
# StagePageP3. The structured plan lives in the DB (stage_plan / task_plan /
# task_graph / task_node tables), not in the artifact files (which hold only refs
# + counts), so this reads the DB directly — the minimal read endpoint the DAG
# view needs (R10-2 handoff §3: 缺则最小新增，归 T19). Honest empty state when P3
# has not produced a plan (available=false, not 404). No LLM call here.
@router.get("/planning-summary")
async def get_planning_summary(project_id: str):
    from app.models.stage_plan import StagePlan, TaskPlan
    from app.models.task_graph import TaskGraph, TaskNode
    from app.core.database import get_session

    svc = get_services()
    if svc.project_service.get(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")

    db = get_session()
    try:
        # latest P3 Stage Plan (version then recency — mirrors stage_service)
        sp = (db.query(StagePlan)
              .filter(StagePlan.project_id == project_id, StagePlan.stage == "p3")
              .order_by(StagePlan.version.desc(), StagePlan.created_at.desc())
              .first())
        if sp is None:
            return SuccessEnvelope(data={
                "available": False,
                "reason": "P3 规划尚未执行。请先完成 P2 评估并批准 Gate 以触发 P3 规划。",
            }, meta=Meta())

        detail = sp.plan_detail or {}
        batch_meta = detail.get("task_plan_batch", {}) or {}
        tps = (db.query(TaskPlan)
               .filter(TaskPlan.stage_plan_ref == sp.stage_plan_id)
               .order_by(TaskPlan.created_at).all())
        # latest TaskGraph for this Stage Plan (Q-R10-3 必生; version then recency)
        tg = (db.query(TaskGraph)
              .filter(TaskGraph.project_id == project_id,
                      TaskGraph.stage_plan_ref == sp.stage_plan_id)
              .order_by(TaskGraph.version.desc(), TaskGraph.created_at.desc())
              .first())
        nodes: list = []
        edges: list = []
        graph_payload = None
        if tg is not None:
            tns = (db.query(TaskNode)
                   .filter(TaskNode.task_graph_id == tg.task_graph_id)
                   .order_by(TaskNode.created_at).all())
            # stable node order = the persisted creation order (= generation index)
            nodes = [{"node_id": n.node_id, "index": i, "title": n.title,
                      "risk_level": n.risk_level, "node_type": n.node_type}
                     for i, n in enumerate(tns)]
            edges = [{"source_node_id": e.get("source_node_id"),
                      "target_node_id": e.get("target_node_id"),
                      "edge_type": e.get("edge_type")}
                     for e in (tg.edges or []) if isinstance(e, dict)]
            # degraded flag is not on the definition row — read best-effort from the
            # p3_task_graph.json artifact (honest: null when absent)
            degraded = None
            try:
                import json as _json
                fp = workspace_path(project_id) / "artifacts" / "p3_task_graph.json"
                if fp.exists():
                    degraded = _json.loads(fp.read_text(encoding="utf-8")).get("degraded")
            except Exception:
                degraded = None
            graph_payload = {
                "task_graph_id": tg.task_graph_id, "graph_status": tg.graph_status,
                "degraded": degraded, "node_count": len(nodes), "edge_count": len(edges),
                "nodes": nodes, "edges": edges,
            }

        return SuccessEnvelope(data={
            "available": True,
            "model_used": detail.get("model_used"),
            "stage_plan": {
                "stage_plan_id": sp.stage_plan_id, "plan_status": sp.plan_status,
                "objective": sp.objective or "", "scope": sp.scope or {},
                "risk_level": sp.risk_level, "permission_boundary": sp.permission_boundary or "",
                "expected_artifacts": sp.expected_artifacts or [],
                "expected_evidence": sp.expected_evidence or [],
                "gate_policy": sp.gate_policy or {},
                "completion_criteria": detail.get("completion_criteria", []),
                "validation_strategy": detail.get("validation_strategy", ""),
            },
            "task_batch": {
                "batch_id": batch_meta.get("batch_id"),
                "batch_objective": batch_meta.get("batch_objective", ""),
                "batch_risk_level": batch_meta.get("batch_risk_level", "L0"),
                "gate_required": bool(batch_meta.get("gate_required", False)),
                "task_count": len(tps),
            },
            "task_plans": [{
                "task_plan_id": t.task_plan_id, "title": t.title or t.objective or "",
                "objective": t.objective or "", "risk_level": t.risk_level,
                "validation_method": t.validation_method or "",
                "scope": t.scope or {},
            } for t in tps],
            "task_graph": graph_payload,
        }, meta=Meta())
    finally:
        db.close()
