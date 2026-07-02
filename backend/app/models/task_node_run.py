"""TaskNodeRun ORM model (R10 T1).

Runtime instance of a Task Node (definition lives in models/task_graph.py TaskNode).
Definition layer vs runtime layer are SEPARATE tables (Q-R10-1 分表裁决): one
TaskNode definition may be executed many times, each execution recorded as one
TaskNodeRun row referencing a TaskGraphRun.

Field domain follows the authoritative state architecture doc
`文档/02-架构设计/03-Project-Run-TaskGraph状态架构.md` §8.2 (Task Node runtime
fields) plus `文档/03-流程与运行时/08-状态与数据模型.md` §8.4 (NodeLoop
self_check_result / acceptance_result). node_status enum = §8.3 (11 states),
projected in app/core/status.py NODE_STATUSES.

This table carries exactly the runtime fields the TaskNode definition delegates
here (see models/task_graph.py TaskNode docstring): node_status, retry_count,
failure_reason, artifact/evidence/trace/audit refs, active_gate_ref,
self_check_result_ref, acceptance_result_ref, completed_at. The NodeLoop 9-step
executor (T2) reads/writes these; T1 only builds the persistence layer.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, JSON, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_node_run_id() -> str:
    return f"tnr-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskNodeRun(Base):
    """Task Node runtime instance (dynamic). State architecture §8.2 runtime fields.

    node_status flows through NODE_STATUSES (§8.3): pending → running →
    self_checking → acceptance_checking → completed, with waiting_gate /
    waiting_resource / retrying / failed / rework_required / skipped branches.
    Static node definition (node_type, input_refs, permission_boundary, ...)
    lives in TaskNode, NOT here.
    """

    __tablename__ = "task_node_run"

    task_node_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_node_run_id)
    # links: which node definition runs, inside which graph run
    node_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_graph_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_graph_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # runtime status (NODE_STATUSES §8.3, 11 states)
    node_status: Mapped[str] = mapped_column(String(32), default="pending")
    # runtime outputs / refs
    artifact_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    trace_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    audit_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # NodeLoop step6 self-check / step8 acceptance result references (08 §8.4)
    self_check_result_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acceptance_result_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # gate / retry / failure
    active_gate_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
