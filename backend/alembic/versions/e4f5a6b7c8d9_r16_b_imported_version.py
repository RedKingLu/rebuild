"""R16-B (E3): resource_entry.imported_version for community resource version traceability

Revision ID: e4f5a6b7c8d9
Revises: 39528fb4d798
Create Date: 2026-07-10

Adds 1 nullable column to resource_entry for the R16-B community resource version
management (E3):
  - imported_version : str | None (导入的社区资源版本；社区 seed 当前仅对最新版本落库有效，
                                历史版本记录用于追溯展示，不阻塞导入主链路)

Idempotent: the app calls Base.metadata.create_all() on boot, so on a live DB the
column may already exist. This migration only adds it if absent.
"""
from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "39528fb4d798"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "resource_entry" not in set(insp.get_table_names()):
        return  # fresh DB → create_all will build it fully
    existing = {c["name"] for c in insp.get_columns("resource_entry")}
    if "imported_version" not in existing:
        op.add_column(
            "resource_entry",
            sa.Column("imported_version", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "resource_entry" not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns("resource_entry")}
    if "imported_version" in existing:
        op.drop_column("resource_entry", "imported_version")
