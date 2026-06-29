"""R9-5-6: Add host_key_fingerprint to remote_host (D-093 SSH host key verification).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "remote_host",
        sa.Column("host_key_fingerprint", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("remote_host", "host_key_fingerprint")
