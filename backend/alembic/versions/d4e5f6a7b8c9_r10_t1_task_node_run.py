"""R10 T1: TaskNodeRun (node runtime instance) table.

Adds the runtime-layer table for Task Node execution instances (Q-R10-1 分表:
definition = task_node in c3d4e5f6a7b8; runtime = task_node_run here). One
TaskNode definition may spawn many TaskNodeRun rows, each bound to a
TaskGraphRun.

SEQUENCING NOTE: R10-2 §5.3 suggested merging T4+T1 into a single R10 migration.
By the time T1 started, the T4 migration (c3d4e5f6a7b8) was already applied to
the dev DB (alembic current == c3d4e5f6a7b8). Editing an applied migration would
require a downgrade/re-upgrade cycle. A separate follow-on revision is the
standard, safer alembic path and is used here instead.

Node status enum (§8.3, 11 states) is projected in app/core/status.py
NODE_STATUSES; field domain follows 02-架构设计/03 §8.2 + 03-流程与运行时/08 §8.4.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_node_run",
        sa.Column("task_node_run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("task_graph_run_id", sa.String(length=36), nullable=False),
        sa.Column("task_graph_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("node_status", sa.String(length=32), server_default="pending"),
        sa.Column("artifact_refs", sa.JSON(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
        sa.Column("trace_refs", sa.JSON(), nullable=True),
        sa.Column("audit_refs", sa.JSON(), nullable=True),
        sa.Column("self_check_result_ref", sa.String(length=36), nullable=True),
        sa.Column("acceptance_result_ref", sa.String(length=36), nullable=True),
        sa.Column("active_gate_ref", sa.String(length=36), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_node_run_id"),
    )
    op.create_index("ix_task_node_run_node_id", "task_node_run", ["node_id"])
    op.create_index("ix_task_node_run_task_graph_run_id", "task_node_run", ["task_graph_run_id"])
    op.create_index("ix_task_node_run_task_graph_id", "task_node_run", ["task_graph_id"])
    op.create_index("ix_task_node_run_project_id", "task_node_run", ["project_id"])
    op.create_index("ix_task_node_run_run_id", "task_node_run", ["run_id"])


def downgrade() -> None:
    op.drop_table("task_node_run")
