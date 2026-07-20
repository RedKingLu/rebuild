"""R17.5 WP-6 (Q-R17.4-3-2): project.migration_target for target runtime environment

Revision ID: f8a1b2c3d4e5
Revises: e4f5a6b7c8d9
Create Date: 2026-07-20

Adds 1 nullable JSON column to project for the R17.5 P0 目标运行环境引导 (WI-ENV):
  - migration_target : dict | None  (用户在引导中点选的目标 CPU 架构 + 目标 OS，
                                     开放可扩展结构，非封闭枚举；用户输入采集非识别逻辑)

Idempotent: the app calls Base.metadata.create_all() on boot, so on a live DB the
column may already exist. This migration only adds it if absent (mirrors the
e4f5a6b7c8d9 / a1b2c3d4e5f6 pattern). revision-id 不与既有 head 冲突（链于 e4f5a6b7c8d9）。
"""
from alembic import op
import sqlalchemy as sa

revision = "f8a1b2c3d4e5"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project" not in set(insp.get_table_names()):
        return  # fresh DB → create_all will build it fully
    existing = {c["name"] for c in insp.get_columns("project")}
    if "migration_target" not in existing:
        op.add_column(
            "project",
            sa.Column("migration_target", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project" not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns("project")}
    if "migration_target" in existing:
        op.drop_column("project", "migration_target")
