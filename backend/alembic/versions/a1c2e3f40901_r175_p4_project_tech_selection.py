"""R17.5-P4-FIX 批3 (D-109): project.tech_selection for the技术路线选型红线

Revision ID: a1c2e3f40901
Revises: f8a1b2c3d4e5
Create Date: 2026-07-24

Adds 1 nullable JSON column to project for the D-109 技术选型红线（与 migration_target
同级 CPU/OS 红线）：
  - tech_selection : dict | None  (LLM 在 P1→P2 gate 基于 P0/P1 识别事实 + migration_target
                                   产出的技术选型建议，用户裁决批准后落此字段成为项目红线，
                                   贯穿注入 P2 规划 / P4 执行并被验收校验；开放可扩展结构)

Idempotent: the app calls Base.metadata.create_all() on boot, so on a live DB the
column may already exist. This migration only adds it if absent (mirrors the
f8a1b2c3d4e5 migration_target pattern). revision-id 不与既有 head 冲突（链于 f8a1b2c3d4e5）。
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c2e3f40901"
down_revision = "f8a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project" not in set(insp.get_table_names()):
        return  # fresh DB → create_all will build it fully
    existing = {c["name"] for c in insp.get_columns("project")}
    if "tech_selection" not in existing:
        op.add_column(
            "project",
            sa.Column("tech_selection", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project" not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns("project")}
    if "tech_selection" in existing:
        op.drop_column("project", "tech_selection")
