"""007 add project table

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "8b9c0d1e2f3a"
down_revision = "7a8b9c0d1e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("project_status", sa.String(length=50), server_default="created"),
        sa.Column("source_type", sa.String(length=50), server_default="local_dir"),
        sa.Column("source_config", sa.JSON(), nullable=True),
        sa.Column("current_stage", sa.String(length=50), nullable=True),
        sa.Column("current_run_id", sa.String(length=36), nullable=True),
        sa.Column("active_gate", sa.String(length=36), nullable=True),
        sa.Column("evidence_gap_count", sa.Integer(), server_default="0"),
        sa.Column("workspace_status", sa.String(length=50), server_default="ready"),
        sa.Column("onboarding_done", sa.Boolean(), server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    op.drop_table("project")
