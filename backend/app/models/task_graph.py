"""TaskGraph ORM models (R10 T4).

Definition layer vs runtime layer are SEPARATE tables (Q-R10-1 分表裁决):
  - TaskGraph / TaskNode  = definition layer (静态; 可版本化 supersedes)
  - TaskGraphRun          = runtime layer  (动态; 一个 TaskGraph 可多次运行)
  - TaskNodeRun           = runtime layer  (归 T1, 见 models/task_node_run.py)

TaskGraph is a data-layer DAG INSIDE a LangGraph node — it does NOT replace the
LangGraph orchestration graph (D-037). Field domains follow the authoritative
state architecture doc `文档/02-架构设计/03-Project-Run-TaskGraph状态架构.md`
§7.2 (TaskGraph) / §8.2 (Task Node) / §9.2 (Task Edge strategy).

Edges are stored as a JSON column on TaskGraph (each edge carries its 8 mandatory
strategy dimensions as a nested dict) rather than a separate table — nodes need a
stable table PK to be referenced by TaskNodeRun, edges do not (they have no
independent runtime state; edge state derives from node state, §9.4). The 8-dim
completeness validation is T5's responsibility, not enforced at the schema level.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, JSON, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_graph_id() -> str:
    return f"tg-{uuid.uuid4().hex[:8]}"


def _new_node_id() -> str:
    return f"tn-{uuid.uuid4().hex[:8]}"


def _new_graph_run_id() -> str:
    return f"tgr-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskGraph(Base):
    """TaskGraph definition (static). State architecture §7.2.

    Runtime state (active/completed/failed/skipped node refs, artifact/evidence
    refs, graph run status) lives in TaskGraphRun, NOT here.
    """

    __tablename__ = "task_graph"

    task_graph_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_graph_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    stage_plan_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_plan_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    # definition-level status: draft / ready / superseded (running/... belong to run)
    graph_status: Mapped[str] = mapped_column(String(32), default="draft")
    # edges: list of {edge_id, source_node_id, target_node_id, edge_type, edge_strategy:{8 dims}}
    edges: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # version chain (Q-R10-1: 分表利于版本链)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskNode(Base):
    """Task Node definition (static). State architecture §8.2.

    Runtime state (node_status, retry_count, failure_reason, artifact/evidence
    refs, active_gate_ref, completed_at) lives in TaskNodeRun (T1), NOT here.
    """

    __tablename__ = "task_node"

    node_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_node_id)
    task_graph_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    node_type: Mapped[str] = mapped_column(String(32), default="execution")
    title: Mapped[str] = mapped_column(String(255), default="")
    input_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    context_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resource_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model_policy_override: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    permission_boundary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(8), default="L0")
    # D-114: 目标产出路径（目标工程相对路径）。P4 worker 据此把产物写入同一目标工程树
    # （output_code/{Project}/{层}/），实现脚手架先行的单一可构建工程；缺失回退 output_code/{node_id}/。
    output_target: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskGraphRun(Base):
    """TaskGraph runtime instance (dynamic). State architecture §7.2 runtime fields.

    One TaskGraph definition may spawn many TaskGraphRun instances (Q-R10-1).
    Node-level runtime rows (TaskNodeRun, T1) reference a TaskGraphRun.
    """

    __tablename__ = "task_graph_run"

    task_graph_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_graph_run_id)
    task_graph_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # runtime status: draft/ready/running/waiting_gate/paused/completed/failed/rework_required/canceled
    graph_status: Mapped[str] = mapped_column(String(32), default="running")
    active_node_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    completed_node_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    failed_node_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    skipped_node_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifact_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    trace_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    audit_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
