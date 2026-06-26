"""008 add git_host table

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa

revision = "9c0d1e2f3a4b"
down_revision = "8b9c0d1e2f3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "git_host",
        sa.Column("git_host_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("repo_url", sa.String(length=1024), nullable=False),
        sa.Column("masked_url", sa.String(length=1024), nullable=True),
        sa.Column("platform", sa.String(length=50), server_default="other"),
        sa.Column("auth_type", sa.String(length=50), server_default="https_token"),
        sa.Column("credential_ref", sa.String(length=36), nullable=True),
        sa.Column("default_branch", sa.String(length=255), server_default="main"),
        sa.Column("clone_path", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="not_configured"),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.PrimaryKeyConstraint("git_host_id"),
    )


def downgrade() -> None:
    op.drop_table("git_host")
