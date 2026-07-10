"""R15-4 (C1+C2): resource_entry distribution fields + soft delete

Revision ID: c4f1a9d7e2b8
Revises: a8b9c0d1e2f3
Create Date: 2026-07-09

Adds 6 columns to resource_entry for the R15 community resource distribution
link (C2) and soft-delete (C1):
  - package_url        : str | None   (社区资源包 URL)
  - checksum_sha256    : str | None   (下载后 sha256 强校验)
  - manifest_json      : JSON | None  (资源构成 manifest)
  - download_count     : int = 0
  - icon_url           : str | None   (来源徽章/图标)
  - deleted_at         : datetime | None (软删除，列表默认过滤 IS NULL)

Idempotent: the app calls Base.metadata.create_all() on boot
(app/core/database.py), so on a live DB the columns may already exist. This
migration only adds columns that are not already present, applying cleanly
whether or not create_all ran first. Fresh revision id (c4f1a9d7e2b8) chosen to
avoid the historical a1b2c3d4e5f6 collision (see R14-6 note).

Review columns (review_status/reviewer/review_notes) are intentionally KEPT
(deprecated, out of the business chain per D-061 修订执行注) — no column drop.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4f1a9d7e2b8"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


_NEW_COLUMNS = {
    "package_url": sa.Column("package_url", sa.String(length=1000), nullable=True),
    "checksum_sha256": sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
    "manifest_json": sa.Column("manifest_json", sa.JSON(), nullable=True),
    "download_count": sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
    "icon_url": sa.Column("icon_url", sa.String(length=1000), nullable=True),
    "deleted_at": sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "resource_entry" not in set(insp.get_table_names()):
        # Fresh DB with no resource_entry yet — create_all will build it fully.
        return
    existing = {c["name"] for c in insp.get_columns("resource_entry")}
    for name, column in _NEW_COLUMNS.items():
        if name not in existing:
            op.add_column("resource_entry", column)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "resource_entry" not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns("resource_entry")}
    for name in reversed(list(_NEW_COLUMNS)):
        if name in existing:
            op.drop_column("resource_entry", name)
