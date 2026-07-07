"""RemoteInvocation — R14-4.

Records every remote execution as a queryable, displayable, auditable session.
R9-5-6's RemoteSSHExecutionProvider returns a transient result dict; nothing
persists. This model is the ExecutionSession (D-051) specialization for the
remote path, holding back-references to stdout/stderr/artifact/trace/audit so
the Workspace UI can render history, logs, status, and next_actions.
"""

import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InvocationTrigger(str, enum.Enum):
    user = "user"
    agent = "agent"
    p_stage = "p_stage"


class InvocationProvider(str, enum.Enum):
    remote_ssh = "remote_ssh"
    container_sandbox = "container_sandbox"


class RemoteInvocation(Base):
    __tablename__ = "remote_invocation"

    invocation_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    binding_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workspace_environment_binding.binding_id"), nullable=True
    )
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    trigger: Mapped[InvocationTrigger] = mapped_column(
        SAEnum(InvocationTrigger), default=InvocationTrigger.user, nullable=False
    )
    provider: Mapped[InvocationProvider] = mapped_column(
        SAEnum(InvocationProvider), default=InvocationProvider.remote_ssh, nullable=False
    )
    # SHA-256[:16] digest of the command (audit-friendly, no raw command stored).
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    exit_code: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), default="L1", nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="success", nullable=False)
    # Back-references to persisted outputs.
    stdout_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stderr_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifact_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    trace_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audit_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
