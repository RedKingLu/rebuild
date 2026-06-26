"""Git account model — OAuth-connected Git platform accounts (GitHub/GitLab/Gitee)."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, LargeBinary, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class GitAccountPlatform(str, enum.Enum):
    github = "github"
    gitlab = "gitlab"
    gitee = "gitee"


class AccountStatus(str, enum.Enum):
    connected = "connected"
    disconnected = "disconnected"
    error = "error"


class GitAccount(Base):
    """An OAuth-connected Git platform account.

    The access_token is AES-256-GCM encrypted (BYOK).
    The token_fingerprint allows dedup detection.
    """

    __tablename__ = "git_account"

    account_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    platform: Mapped[GitAccountPlatform] = mapped_column(
        SAEnum(GitAccountPlatform), nullable=False
    )
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted OAuth access token
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[AccountStatus] = mapped_column(
        SAEnum(AccountStatus), default=AccountStatus.connected, nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(255), default="default")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    enabled: Mapped[bool] = mapped_column(default=True)
