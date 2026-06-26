"""Integration config model — generic integration configuration store."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class IntegrationType(str, enum.Enum):
    git = "git"
    remote = "remote"
    opencode = "opencode"
    feishu = "feishu"
    future = "future"


class IntegrationStatus(str, enum.Enum):
    connected = "connected"
    configured_not_verified = "configured_not_verified"
    credential_missing = "credential_missing"
    not_configured = "not_configured"
    error = "error"
    disabled = "disabled"


class IntegrationConfig(Base):
    __tablename__ = "integration_config"

    config_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    integration_type: Mapped[IntegrationType] = mapped_column(
        SAEnum(IntegrationType), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Integration-specific configuration dict
    status: Mapped[IntegrationStatus] = mapped_column(
        SAEnum(IntegrationStatus), default=IntegrationStatus.not_configured, nullable=False
    )
    credential_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
