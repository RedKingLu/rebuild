"""R9-5-5: add external_platform_scope to Project

Revision ID: a1b2c3d4e5f6
Revises: 58ae31ca2d2b
Create Date: 2026-06-28 10:00:00.000000

D-088 / R9-5-5: Project-level delegation scope — controls how much of the P0-P6
flow is delegated to an external platform (OpenCode etc.).
Values: none / coding_only / all_stages (default: none).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '58ae31ca2d2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project', sa.Column(
        'external_platform_scope',
        sa.String(length=32),
        nullable=False,
        server_default='none',
    ))


def downgrade() -> None:
    op.drop_column('project', 'external_platform_scope')
