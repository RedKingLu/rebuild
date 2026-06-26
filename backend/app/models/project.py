"""Project model — persisted project with source configuration."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class ProjectStatus(str, enum.Enum):
    created = "created"
    active = "active"
    running = "running"
    archived = "archived"


class SourceType(str, enum.Enum):
    local_dir = "local_dir"
    git = "git"
    zip_source = "zip"
    github = "github"
    manual = "manual"


class Project(Base):
    __tablename__ = "project"

    project_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus), default=ProjectStatus.created, nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType), default=SourceType.local_dir, nullable=False
    )
    source_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # source_config examples:
    #   {"path": "/home/user/project"}  for local_dir
    #   {"git_host_id": "uuid", "branch": "main", "subpath": "/"}  for git
    #   {} for manual
    current_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_gate: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence_gap_count: Mapped[int] = mapped_column(default=0)
    workspace_status: Mapped[str | None] = mapped_column(String(50), default="ready")
    onboarding_done: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
