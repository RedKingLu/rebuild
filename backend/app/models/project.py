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
    coding_agent_ref: Mapped[str | None] = mapped_column(
        String(36), nullable=True, default=None
    )  # D-078/R9-3A: FK → coding_agent_config.agent_id; default None = platform_agent
    # D-088 / R9-5-5: delegation scope — how much of the P0-P6 flow is delegated to
    # an external platform (e.g. OpenCode).  Paired with coding_agent_ref.
    #   none         → self-hosted platform handles everything (default)
    #   coding_only  → external platform handles P4 coding tasks only
    #   all_stages   → external platform handles all P0-P6 stages
    external_platform_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    # D-098: model strategy mode for this project.
    #   global_unified → all calls use global_model_ref (one model for everything)
    #   custom         → per-agent model (AgentDefinition.model_policy_ref), else system default
    # 默认 global_unified + null ref ⇒ 回落系统默认策略（动态 user_strategies.yaml override），不改现状。
    model_strategy_mode: Mapped[str] = mapped_column(String(32), default="global_unified", server_default="global_unified")
    global_model_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
