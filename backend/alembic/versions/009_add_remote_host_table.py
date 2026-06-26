"""009 add remote_host table

Revision ID: 0d1e2f3a4b5c
Revises: 9c0d1e2f3a4b
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0d1e2f3a4b5c"
down_revision = "9c0d1e2f3a4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_host",
        sa.Column("remote_host_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("host_type", sa.String(length=50), server_default="virtual_machine"),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("masked_address", sa.String(length=255), nullable=True),
        sa.Column("port", sa.Integer(), server_default="22"),
        sa.Column("os_name", sa.String(length=100), nullable=True),
        sa.Column("credential_ref", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="unconfigured"),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.PrimaryKeyConstraint("remote_host_id"),
    )


def downgrade() -> None:
    op.drop_table("remote_host")
