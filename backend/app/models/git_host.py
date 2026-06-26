"""Git host model — connected Git repository configuration."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class GitHostStatus(str, enum.Enum):
    connected = "connected"
    disconnected = "disconnected"
    error = "error"
    cloning = "cloning"
    not_configured = "not_configured"


class GitPlatform(str, enum.Enum):
    github = "github"
    gitlab = "gitlab"
    gitee = "gitee"
    other = "other"


class GitHost(Base):
    __tablename__ = "git_host"

    git_host_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # masked_url is derived from repo_url with credentials stripped
    masked_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    platform: Mapped[GitPlatform] = mapped_column(
        SAEnum(GitPlatform), default=GitPlatform.other, nullable=False
    )
    auth_type: Mapped[str] = mapped_column(String(50), default="https_token")
    # "https_token" or "ssh_key"
    credential_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # FK reference to credential table (not enforced as FK for flexibility with test isolation)
    default_branch: Mapped[str | None] = mapped_column(String(255), default="main")
    clone_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Local clone path: data_dir/integration-repos/{git_host_id}/
    status: Mapped[GitHostStatus] = mapped_column(
        SAEnum(GitHostStatus), default=GitHostStatus.not_configured, nullable=False
    )
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    enabled: Mapped[bool] = mapped_column(default=True)
