"""005 simplify skill category enum and add enabled field

Revision ID: 6f7a8b9c0d1e
Revises: 5e6f7a8b9c0d
Create Date: 2026-06-26

Changes:
- SkillCategory enum simplified to: common,p0,p1,p2,p3,p4,p5,p6,other
- Add enabled column to skill_definition (default True)
- Migrate existing rows: all old categories -> 'common'
"""
from alembic import op
import sqlalchemy as sa

revision = "6f7a8b9c0d1e"
down_revision = "5e6f7a8b9c0d"
branch_labels = None
depends_on = None

NEW_CATS = ("common", "p0", "p1", "p2", "p3", "p4", "p5", "p6", "other")


def upgrade() -> None:
    bind = op.get_bind()

    # SQLite: column type stored as TEXT for enums, just update values + add column
    # Step 1: set all existing categories to 'common' (migration decision)
    bind.execute(sa.text("UPDATE skill_definition SET category = 'common'"))

    # Step 2: add enabled column
    op.add_column(
        "skill_definition",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    # Drop enabled column; category values already lost — cannot restore original enum
    op.drop_column("skill_definition", "enabled")
