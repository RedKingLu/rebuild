"""010 add integration_config table

Revision ID: 1e2f3a4b5c6d
Revises: 0d1e2f3a4b5c
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "1e2f3a4b5c6d"
down_revision = "0d1e2f3a4b5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_config",
        sa.Column("config_id", sa.String(length=36), nullable=False),
        sa.Column("integration_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="not_configured"),
        sa.Column("credential_ref", sa.String(length=36), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("config_id"),
    )


def downgrade() -> None:
    op.drop_table("integration_config")
