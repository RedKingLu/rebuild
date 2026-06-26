"""004_add_mcp_server — Phase 12: MCP server management table.

Revision ID: 5e6f7a8b9c0d
Revises: 4d5e6f7a8b9c
Create Date: 2026-06-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '5e6f7a8b9c0d'
down_revision: Union[str, None] = '4d5e6f7a8b9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS mcp_server (
            mcp_id VARCHAR(36) NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT DEFAULT '',
            transport VARCHAR(20) DEFAULT 'stdio' NOT NULL,
            command VARCHAR(500),
            args JSON,
            env_vars JSON,
            sse_url VARCHAR(500),
            status VARCHAR(20) DEFAULT 'disconnected',
            last_checked_at DATETIME,
            error_message TEXT,
            tools JSON,
            origin VARCHAR(50) DEFAULT 'user_created',
            enabled BOOLEAN DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME,
            PRIMARY KEY (mcp_id)
        )
    """)


def downgrade() -> None:
    op.drop_table('mcp_server')
