"""R10 T8: PlanDelta table.

Adds the plan-change-tracking table (规范 §5). Records why a plan was superseded
(§5.4-3: never silently overwrite). Follows the T1 sequencing note — a separate
follow-on revision after the T1 migration.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_delta",
        sa.Column("plan_delta_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("source_plan_ref", sa.String(length=36), nullable=True),
        sa.Column("target_plan_ref", sa.String(length=36), nullable=True),
        sa.Column("delta_type", sa.String(length=32), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("risk_impact", sa.String(length=64), nullable=True),
        sa.Column("permission_impact", sa.String(length=128), nullable=True),
        sa.Column("artifact_impact", sa.JSON(), nullable=True),
        sa.Column("evidence_impact", sa.JSON(), nullable=True),
        sa.Column("gate_required", sa.Boolean(), server_default=sa.false()),
        sa.Column("audit_ref", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("plan_delta_id"),
    )
    op.create_index("ix_plan_delta_project_id", "plan_delta", ["project_id"])
    op.create_index("ix_plan_delta_run_id", "plan_delta", ["run_id"])
    op.create_index("ix_plan_delta_source_plan_ref", "plan_delta", ["source_plan_ref"])


def downgrade() -> None:
    op.drop_table("plan_delta")
