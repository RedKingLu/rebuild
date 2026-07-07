"""R14-4: WorkspaceEnvironmentBinding & RemoteInvocation schemas."""

from typing import Optional
from pydantic import BaseModel, Field

from app.models.remote_invocation import InvocationProvider, InvocationTrigger
from app.models.workspace_environment_binding import BindingStatus


# ── Binding ──────────────────────────────────────────────────────────────

class BindingCreateRequest(BaseModel):
    remote_host_id: str
    is_default: bool = False
    remote_workdir: str | None = None


class BindingResponse(BaseModel):
    binding_id: str
    workspace_id: str
    remote_host_id: str
    is_default: bool = False
    remote_workdir: str | None = None
    status: str = "active"
    last_invoked_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    source_status: str = "real"
    capability_status: str = "available"


class EnvironmentBlock(BaseModel):
    """The `environment` block in workspace.json — single source of truth."""

    default_binding_id: str | None = None
    bindings: list[str] = Field(default_factory=list)  # binding_ids


# ── Invocation ──────────────────────────────────────────────────────────

class InvocationResponse(BaseModel):
    invocation_id: str
    binding_id: str | None = None
    workspace_id: str
    trigger: str = "user"
    provider: str = "remote_ssh"
    command_digest: str = ""
    exit_code: int = -1
    risk_level: str = "L1"
    elapsed_ms: int = 0
    status: str = "success"
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    artifact_ref: str | None = None
    trace_ref: str | None = None
    audit_ref: str | None = None
    error_message: str | None = None
    started_at: str = ""
    created_at: str = ""
    source_status: str = "real"
    capability_status: str = "available"
    next_actions: list[str] = Field(default_factory=list)
