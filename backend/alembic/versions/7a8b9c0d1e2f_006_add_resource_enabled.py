"""006 add enabled to resource_entry

Revision ID: 7a8b9c0d1e2f
Revises: 6f7a8b9c0d1e
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "7a8b9c0d1e2f"
down_revision = "6f7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resource_entry",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("resource_entry", "enabled")
