"""RemoteInvocation service — R14-4 (WP4).

Persists each remote execution as a queryable RemoteInvocation record, with
stdout/stderr saved to the project workspace logs/ directory so the Workspace
UI can render history, logs, status, and next_actions.

The remote executor (remote_executor.py) returns a transient result dict; this
service is what makes the result durable and displayable.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models.remote_invocation import (
    RemoteInvocation, InvocationTrigger, InvocationProvider,
)
from app.services.workspace_service import workspace_path

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _logs_dir(project_id: str) -> Path:
    return workspace_path(project_id) / "logs" / "remote"


def _write_ref(project_id: str, invocation_id: str, suffix: str, content: str) -> str:
    """Save content under logs/remote/<invocation_id>.<suffix>; return rel ref."""
    d = _logs_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    ref = f"logs/remote/{invocation_id}.{suffix}"
    (workspace_path(project_id) / ref).write_text(
        content or "", encoding="utf-8", errors="replace",
    )
    return ref


def record_invocation(
    *,
    db,
    workspace_id: str,
    binding_id: str | None,
    provider: str,
    command_digest: str,
    exit_code: int,
    risk_level: str,
    elapsed_ms: int,
    stdout: str,
    stderr: str,
    trigger: str = "user",
    status: str | None = None,
    audit_ref: str | None = None,
    trace_ref: str | None = None,
    artifact_ref: str | None = None,
    error_message: str | None = None,
) -> RemoteInvocation:
    """Persist a RemoteInvocation + save stdout/stderr to workspace logs.
    Returns the created RemoteInvocation.

    R14-6 (B-R14-INV-REF-1): trace_ref / audit_ref / artifact_ref are now real
    back-link parameters. Callers on the P5 remote path pass the tracer trace_id
    and auditor audit_id so the columns are no longer dead.
    """
    invocation_id = str(uuid.uuid4())
    # Cap stored copy (matches existing 5000 cap in p5_command_service).
    stdout_ref = _write_ref(workspace_id, invocation_id, "out", stdout[:65536])
    stderr_ref = _write_ref(workspace_id, invocation_id, "err", stderr[:65536])

    if status is None:
        status = "success" if exit_code == 0 else "failure"

    inv = RemoteInvocation(
        invocation_id=invocation_id,
        binding_id=binding_id,
        workspace_id=workspace_id,
        trigger=InvocationTrigger(trigger) if trigger in [e.value for e in InvocationTrigger] else InvocationTrigger.user,
        provider=InvocationProvider(provider) if provider in [e.value for e in InvocationProvider] else InvocationProvider.remote_ssh,
        command_digest=command_digest or "",
        exit_code=exit_code,
        risk_level=risk_level or "L1",
        elapsed_ms=elapsed_ms,
        status=status,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        artifact_ref=artifact_ref,
        trace_ref=trace_ref,
        audit_ref=audit_ref,
        error_message=error_message,
        started_at=_now(),
        created_at=_now(),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv
