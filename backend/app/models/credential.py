"""Credential model — encrypted API key storage (BYOK)."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, LargeBinary, DateTime, Enum as SAEnum, Text
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class KeySource(str, enum.Enum):
    system = "system"
    tenant = "tenant"
    user = "user"
    byok = "byok"
    env_fallback = "env_fallback"


class CredentialStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    rotated = "rotated"
    revoked = "revoked"


class Credential(Base):
    __tablename__ = "credential"

    credential_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    key_source: Mapped[KeySource] = mapped_column(
        SAEnum(KeySource), default=KeySource.user, nullable=False
    )
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256 hex
    status: Mapped[CredentialStatus] = mapped_column(
        SAEnum(CredentialStatus), default=CredentialStatus.active, nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(255), default="default")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
