"""WorkspaceEnvironmentBinding — R14-4.

Binds one or more RemoteHosts to a Project Workspace, with exactly one default.
A lightweight realization of the "binding layer" that R9-5-6's execution base
lacks: the execution base (RemoteHost / RemoteSSHExecutionProvider) is real, but
nothing ties a host to a workspace or selects a default. This model does.

D-051 三对象: Workspace (files) / EnvironmentProfile (declaration) / ExecutionSession.
This binding is the missing link between Workspace and the remote execution base.
"""

import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BindingStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    error = "error"


class WorkspaceEnvironmentBinding(Base):
    __tablename__ = "workspace_environment_binding"

    binding_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    remote_host_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("remote_host.remote_host_id"), nullable=False
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Remote working directory. Default "/workspace/{workspace_id}" if None.
    remote_workdir: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[BindingStatus] = mapped_column(
        SAEnum(BindingStatus), default=BindingStatus.active, nullable=False
    )
    last_invoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        # One default binding per workspace.
        UniqueConstraint("workspace_id", "is_default", name="uq_binding_workspace_default"),
    )
