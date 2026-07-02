"""StagePlan / TaskPlan ORM models (R10 T4).

Field domains follow the authoritative state architecture doc
`文档/02-架构设计/03-Project-Run-TaskGraph状态架构.md` §5.2 (Stage Plan) and
§6.2 (Task Plan). Task Plan Batch (§6.3) is represented here as a lightweight
`batch_id` grouping column on TaskPlan rather than a separate table — batches are
a grouping concern, not an independently-persisted entity this phase (T4 keeps
the 5-table boundary from R10-2 §2). PlanDelta (§5/§version chain) is a separate
model added in T8 (models/plan_delta.py).

StagePlan carries a version chain (version + supersedes) per Q-R10-1 (分表利于
版本链). These are definition-layer tables; there is no separate runtime instance
table for plans (unlike TaskGraph→TaskGraphRun) — a plan's execution progress is
tracked via its status field and the TaskGraphRun it drives.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, JSON, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_stage_plan_id() -> str:
    return f"sp-{uuid.uuid4().hex[:8]}"


def _new_task_plan_id() -> str:
    return f"tp-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StagePlan(Base):
    """Stage Plan (阶段级计划). State architecture §5.2."""

    __tablename__ = "stage_plan"

    stage_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_stage_plan_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # draft/under_review/approved/rejected/superseded/executing/completed/failed
    plan_status: Mapped[str] = mapped_column(String(32), default="draft")
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(8), default="L0")
    permission_boundary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_artifacts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected_evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected_trace: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected_audit: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gate_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # compat with existing schema (StagePlanRequest.plan_summary / plan_detail)
    plan_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # version chain (Q-R10-1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskPlan(Base):
    """Task Plan (任务级计划). State architecture §6.2.

    `batch_id` groups task plans into a Task Plan Batch (§6.3) without a separate
    table. `stage_plan_ref` links to the owning StagePlan (§6.4-1: 不得越过 Stage Plan).
    """

    __tablename__ = "task_plan"

    task_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_task_plan_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    stage_plan_ref: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    inputs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected_outputs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(8), default="L0")
    permission_boundary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    required_resources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model_policy_override: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validation_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_artifacts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected_evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # compat with existing schema (TaskPlanResponse.title / description)
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
