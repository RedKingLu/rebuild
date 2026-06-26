"""Add git_account table for OAuth-connected Git platform accounts.

Revision ID: 1f3a4b5c6d7e
Revises: 1e2f3a4b5c6d (010)
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = '1f3a4b5c6d7e'
down_revision: str | None = '1e2f3a4b5c6d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'git_account',
        sa.Column('account_id', sa.String(36), primary_key=True),
        sa.Column('platform', sa.String(50), nullable=False, server_default='github'),
        sa.Column('username', sa.String(255), nullable=False),
        sa.Column('encrypted_token', sa.LargeBinary, nullable=False),
        sa.Column('token_fingerprint', sa.String(64), nullable=False),
        sa.Column('avatar_url', sa.String(1024), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='connected'),
        sa.Column('tenant_id', sa.String(255), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_table('git_account')
