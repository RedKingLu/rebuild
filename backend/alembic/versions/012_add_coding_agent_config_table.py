"""Add coding_agent_config table (D-077 / R8-5).

Revision ID: 2a3b4c5d6e7f
Revises: 1f3a4b5c6d7e (011)
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa

revision = "2a3b4c5d6e7f"
down_revision = "1f3a4b5c6d7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coding_agent_config",
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("agent_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("invoke_mode", sa.String(length=20), server_default="cli"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("credential_ref", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="not_configured"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_id"),
    )


def downgrade() -> None:
    op.drop_table("coding_agent_config")
