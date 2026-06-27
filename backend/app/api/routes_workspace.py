"""Workspace API routes — aggregate + file read/write + material tree + execution.

R8: Adds real file system scanning, file read/write with boundary protection,
material tree, and terminal execution endpoint.
"""

import asyncio
import os
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

    # Create or reuse execution session
    session_id = req.session_id
    if not session_id:
        import uuid
        session_id = f"es-{uuid.uuid4().hex[:8]}"
        # Write initial session record
        from app.services.workspace_service import workspace_path
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
        from app.services.workspace_service import workspace_path
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
        pass

    return SuccessEnvelope(data={
        "session_id": session_id,
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
                pass
    return SuccessEnvelope(data={"sessions": sessions[:50]}, meta=Meta())
