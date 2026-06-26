"""Remote host model — SSH-managed remote resource (VM/physical/container)."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class HostType(str, enum.Enum):
    virtual_machine = "virtual_machine"
    physical_machine = "physical_machine"
    container = "container"


class RemoteHostStatus(str, enum.Enum):
    connected = "connected"
    disconnected = "disconnected"
    unconfigured = "unconfigured"
    error = "error"
    testing = "testing"


class RemoteHost(Base):
    __tablename__ = "remote_host"

    remote_host_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host_type: Mapped[HostType] = mapped_column(
        SAEnum(HostType), default=HostType.virtual_machine, nullable=False
    )
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    # Encrypted at rest via BYOK — this stores the ciphertext
    masked_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Display version: "192.168.****.100"
    port: Mapped[int] = mapped_column(Integer, default=22)
    os_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # FK reference to credential table
    status: Mapped[RemoteHostStatus] = mapped_column(
        SAEnum(RemoteHostStatus), default=RemoteHostStatus.unconfigured, nullable=False
    )
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    enabled: Mapped[bool] = mapped_column(default=True)
