"""002_add_call_log — FB-006: persistent model call log table.

Revision ID: 3a7b2c1d4e5f
Revises: 2ca8561394ae
Create Date: 2026-06-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a7b2c1d4e5f'
down_revision: Union[str, None] = '2ca8561394ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'call_log',
        sa.Column('model_call_id', sa.String(36), primary_key=True),
        sa.Column('provider_id', sa.String(100), nullable=False, index=True),
        sa.Column('profile_id', sa.String(200), nullable=False),
        sa.Column('strategy_id', sa.String(100), default='system-default'),
        sa.Column('selected_model', sa.String(200), nullable=False),
        sa.Column('selection_reason', sa.String(100), default=''),
        sa.Column('status', sa.String(20), nullable=False, index=True),
        sa.Column('latency_ms', sa.Float, default=0),
        sa.Column('error_category', sa.String(50), default=''),
        sa.Column('retry_count', sa.Integer, default=0),
        sa.Column('fallback_used', sa.Integer, default=0),
        sa.Column('prompt_tokens', sa.Integer, default=0),
        sa.Column('completion_tokens', sa.Integer, default=0),
        sa.Column('total_tokens', sa.Integer, default=0),
        sa.Column('source', sa.String(50), default='api'),
        sa.Column('created_at', sa.DateTime(timezone=True), index=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('call_log')
