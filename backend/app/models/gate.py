"""Gate model — persisted Gate with decision tracking."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_gate_id() -> str:
    return f"gate-{uuid.uuid4().hex[:6]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Gate(Base):
    __tablename__ = "p_gate"

    gate_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_gate_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gate_type: Mapped[str] = mapped_column(String(32), default="stage_promotion")
    gate_status: Mapped[str] = mapped_column(String(32), default="waiting_decision")
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(8), default="L0")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    artifact_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    trace_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    audit_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transition_mode: Mapped[str] = mapped_column(String(32), default="real")
    # WP-6: LangGraph checkpoint link — thread_id==run_id; non-null iff graph created this Gate
    checkpoint_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interrupt_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
