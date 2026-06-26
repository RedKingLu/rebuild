"""003_add_agent_fields — Phase 12: category, enabled, prompt, bound resources.

Revision ID: 4d5e6f7a8b9c
Revises: 3a7b2c1d4e5f
Create Date: 2026-06-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '4d5e6f7a8b9c'
down_revision: Union[str, None] = '3a7b2c1d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to agent_definition
    op.execute("ALTER TABLE agent_definition ADD COLUMN category VARCHAR(20) DEFAULT 'system'")
    op.execute("ALTER TABLE agent_definition ADD COLUMN enabled BOOLEAN DEFAULT 1")
    op.execute("ALTER TABLE agent_definition ADD COLUMN custom_prompt TEXT")
    op.execute("ALTER TABLE agent_definition ADD COLUMN bound_skills JSON")
    op.execute("ALTER TABLE agent_definition ADD COLUMN bound_tools JSON")
    op.execute("ALTER TABLE agent_definition ADD COLUMN bound_mcps JSON")
    op.execute("ALTER TABLE agent_definition ADD COLUMN origin VARCHAR(50) DEFAULT 'user_created'")
    # Update existing agents: node_worker/acceptance/auto_review/conversation_gate → system
    op.execute("UPDATE agent_definition SET category = 'system' WHERE agent_type != 'expert'")
    op.execute("UPDATE agent_definition SET category = 'expert' WHERE agent_type = 'expert' OR category IS NULL OR category = ''")


def downgrade() -> None:
    # SQLite doesn't support DROP COLUMN easily; skip for now
    pass
