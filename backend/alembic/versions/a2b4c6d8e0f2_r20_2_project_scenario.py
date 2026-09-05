"""R20-2-01 (D-117③): project.scenario for the project's refactoring scenario id

Revision ID: a2b4c6d8e0f2
Revises: c2d4f5a6b7c8
Create Date: 2026-09-05

Adds 1 nullable String(64) column to project for R20-2 (scenario 一等公民):
  - scenario : str | None  (自由文本场景 id，取值集合由 source/skills/scenarios/<id>/
                            目录动态决定，不是封闭枚举；NULL = 用户尚未选择场景)

revision id 不与既有 21 个 id 冲突（施工前实跑防撞：
  grep -l 'revision = "a2b4c6d8e0f2"' alembic/versions/*.py  →  0 hits；
  且全量清单中无任何 id 以 "a2" 开头，故短前缀匹配亦不歧义）。

Idempotent: the app calls Base.metadata.create_all() on boot, so on a live DB the
column may already exist. This migration only adds it if absent (mirrors the
f8a1b2c3d4e5 / e4f5a6b7c8d9 pattern).

不用 batch_alter_table：SQLite 的 ADD COLUMN 原生支持（O(1) 元数据操作，不重写表），
加 batch 会触发表重建 = 无谓风险（对齐 R20-2 施工计划 §7.1 N-1）。
"""
from alembic import op
import sqlalchemy as sa

revision = "a2b4c6d8e0f2"
down_revision = "c2d4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project" not in set(insp.get_table_names()):
        return  # fresh DB → create_all will build it fully
    existing = {c["name"] for c in insp.get_columns("project")}
    if "scenario" not in existing:
        op.add_column(
            "project",
            sa.Column("scenario", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "project" not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns("project")}
    if "scenario" in existing:
        op.drop_column("project", "scenario")
