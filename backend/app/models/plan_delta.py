"""PlanDelta ORM model (R10 T8).

A Plan Delta records the difference between a plan (Stage Plan / Task Plan /
TaskGraph) and its previously-approved version — the change-tracking primitive
required by `文档/03-流程与运行时/02-StagePlan-TaskPlan-TaskGraph规范.md` §5.

Field domain = §5.3 (13 fields) + provenance (project/run/stage/created_*).
The version chain itself lives on the plan rows (StagePlan.version/supersedes,
T4); PlanDelta is the immutable audit of WHY a supersede happened (§5.4-3: never
silently overwrite — the superseded plan is preserved and the reason recorded
here). Trigger conditions (§5.2, 9 kinds) map to `delta_type` (see
plan_delta_service.DELTA_TYPES).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_plan_delta_id() -> str:
    return f"pd-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlanDelta(Base):
    """Plan change record (规范 §5.3). Immutable once written — the fact chain."""

    __tablename__ = "plan_delta"

    plan_delta_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_plan_delta_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # the plan being changed → the new version it produced (§9 supersedes chain)
    source_plan_ref: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    target_plan_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    delta_type: Mapped[str] = mapped_column(String(32), nullable=False)  # §5.2 → DELTA_TYPES
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_impact: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permission_impact: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_impact: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_impact: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gate_required: Mapped[bool] = mapped_column(Boolean, default=False)
    audit_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
