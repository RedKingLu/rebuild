"""R10 T4: TaskGraph + StagePlan + TaskPlan ORM tables.

Creates the 5 definition/runtime tables backing P3 planning (Q-R10-1 分表):
  task_graph (定义) / task_node (定义) / task_graph_run (运行实例) /
  stage_plan / task_plan.
TaskNodeRun (node runtime instance) is added in T1.

MERGE POINT: R9 left the migration tree with two heads
  - 3a72e126a753 (r9_run_and_gate_tables)
  - b2c3d4e5f6a7 (r956 execution_isolation)
both descending from branchpoint 58ae31ca2d2b. `alembic upgrade head` failed with
"Multiple head revisions are present". This R10 migration unifies them via a tuple
down_revision (P1 fix folded into T4 per 用户 P0/P1 即时修复原则).

Revision ID: c3d4e5f6a7b8
Revises: 3a72e126a753, b2c3d4e5f6a7
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = ("3a72e126a753", "b2c3d4e5f6a7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── task_graph (definition) ──────────────────────────────────────────
    op.create_table(
        "task_graph",
        sa.Column("task_graph_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("stage_plan_ref", sa.String(length=36), nullable=True),
        sa.Column("task_plan_refs", sa.JSON(), nullable=True),
        sa.Column("title", sa.String(length=255), server_default=""),
        sa.Column("graph_status", sa.String(length=32), server_default="draft"),
        sa.Column("edges", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("supersedes", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_graph_id"),
    )
    op.create_index("ix_task_graph_project_id", "task_graph", ["project_id"])
    op.create_index("ix_task_graph_run_id", "task_graph", ["run_id"])

    # ── task_node (definition) ───────────────────────────────────────────
    op.create_table(
        "task_node",
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("task_graph_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("node_type", sa.String(length=32), server_default="execution"),
        sa.Column("title", sa.String(length=255), server_default=""),
        sa.Column("input_refs", sa.JSON(), nullable=True),
        sa.Column("context_refs", sa.JSON(), nullable=True),
        sa.Column("resource_refs", sa.JSON(), nullable=True),
        sa.Column("model_policy_override", sa.String(length=128), nullable=True),
        sa.Column("tool_policy", sa.JSON(), nullable=True),
        sa.Column("permission_boundary", sa.String(length=64), nullable=True),
        sa.Column("risk_level", sa.String(length=8), server_default="L0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("node_id"),
    )
    op.create_index("ix_task_node_task_graph_id", "task_node", ["task_graph_id"])
    op.create_index("ix_task_node_project_id", "task_node", ["project_id"])

    # ── task_graph_run (runtime instance) ────────────────────────────────
    op.create_table(
        "task_graph_run",
        sa.Column("task_graph_run_id", sa.String(length=36), nullable=False),
        sa.Column("task_graph_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("graph_status", sa.String(length=32), server_default="running"),
        sa.Column("active_node_refs", sa.JSON(), nullable=True),
        sa.Column("completed_node_refs", sa.JSON(), nullable=True),
        sa.Column("failed_node_refs", sa.JSON(), nullable=True),
        sa.Column("skipped_node_refs", sa.JSON(), nullable=True),
        sa.Column("artifact_refs", sa.JSON(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
        sa.Column("trace_refs", sa.JSON(), nullable=True),
        sa.Column("audit_refs", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_graph_run_id"),
    )
    op.create_index("ix_task_graph_run_task_graph_id", "task_graph_run", ["task_graph_id"])
    op.create_index("ix_task_graph_run_project_id", "task_graph_run", ["project_id"])
    op.create_index("ix_task_graph_run_run_id", "task_graph_run", ["run_id"])

    # ── stage_plan ───────────────────────────────────────────────────────
    op.create_table(
        "stage_plan",
        sa.Column("stage_plan_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("plan_status", sa.String(length=32), server_default="draft"),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("risk_level", sa.String(length=8), server_default="L0"),
        sa.Column("permission_boundary", sa.String(length=64), nullable=True),
        sa.Column("expected_artifacts", sa.JSON(), nullable=True),
        sa.Column("expected_evidence", sa.JSON(), nullable=True),
        sa.Column("expected_trace", sa.JSON(), nullable=True),
        sa.Column("expected_audit", sa.JSON(), nullable=True),
        sa.Column("gate_policy", sa.JSON(), nullable=True),
        sa.Column("plan_summary", sa.Text(), nullable=True),
        sa.Column("plan_detail", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("supersedes", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("stage_plan_id"),
    )
    op.create_index("ix_stage_plan_project_id", "stage_plan", ["project_id"])
    op.create_index("ix_stage_plan_run_id", "stage_plan", ["run_id"])

    # ── task_plan ────────────────────────────────────────────────────────
    op.create_table(
        "task_plan",
        sa.Column("task_plan_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("stage_plan_ref", sa.String(length=36), nullable=True),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=True),
        sa.Column("expected_outputs", sa.JSON(), nullable=True),
        sa.Column("risk_level", sa.String(length=8), server_default="L0"),
        sa.Column("permission_boundary", sa.String(length=64), nullable=True),
        sa.Column("required_resources", sa.JSON(), nullable=True),
        sa.Column("model_policy_override", sa.String(length=128), nullable=True),
        sa.Column("validation_method", sa.String(length=64), nullable=True),
        sa.Column("expected_artifacts", sa.JSON(), nullable=True),
        sa.Column("expected_evidence", sa.JSON(), nullable=True),
        sa.Column("title", sa.String(length=255), server_default=""),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_plan_id"),
    )
    op.create_index("ix_task_plan_project_id", "task_plan", ["project_id"])
    op.create_index("ix_task_plan_stage_plan_ref", "task_plan", ["stage_plan_ref"])
    op.create_index("ix_task_plan_batch_id", "task_plan", ["batch_id"])


def downgrade() -> None:
    op.drop_table("task_plan")
    op.drop_table("stage_plan")
    op.drop_table("task_graph_run")
    op.drop_table("task_node")
    op.drop_table("task_graph")
