"""Run model — persisted Run with stage status tracking."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_id() -> str:
    return f"run-{uuid.uuid4().hex[:6]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_status: Mapped[str] = mapped_column(String(32), default="created")
    current_stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(16), default="plan")
    stage_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_status: Mapped[str] = mapped_column(String(32), default="real")
    capability_status: Mapped[str] = mapped_column(String(32), default="available")
    transition_mode: Mapped[str] = mapped_column(String(32), default="real")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
