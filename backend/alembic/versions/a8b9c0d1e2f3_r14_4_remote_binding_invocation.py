"""R14-4: workspace_environment_binding + remote_invocation + remote_host extension

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-07

R14-6 fix (B-R14-MIGR-COLLIDE-1): revision id was previously "a1b2c3d4e5f6",
which collided with R9-5-5 (a1b2c3d4e5f6_r955_add_external_platform_scope).
Renamed to the globally unique "a8b9c0d1e2f3"; down_revision remains the real
single head f7a8b9c0d1e2 (R13-4 fusion_profile).

R14-6 fix (B-R14-MULTIBIND-1): default-binding uniqueness is now a PARTIAL
unique index (WHERE is_default = 1) instead of a full UNIQUE(workspace_id,
is_default) constraint, so a workspace may hold many non-default bindings while
still allowing at most one default.
"""
from alembic import op
import sqlalchemy as sa

revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # R14-6 (B-R14-REMOTEHOST-COL-1): the app calls Base.metadata.create_all() on
    # engine boot (app/core/database.py), so on a live DB the two new tables may
    # already exist while remote_host still lacks the new columns (create_all does
    # not ALTER existing tables). This migration is therefore idempotent: it only
    # creates tables / indexes / columns that are not already present, so it applies
    # cleanly whether or not create_all ran first.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    def _indexes(table: str) -> set[str]:
        try:
            return {ix["name"] for ix in insp.get_indexes(table)}
        except Exception:
            return set()

    def _columns(table: str) -> set[str]:
        try:
            return {c["name"] for c in insp.get_columns(table)}
        except Exception:
            return set()

    # ── workspace_environment_binding ──────────────────────────────────────
    if "workspace_environment_binding" not in existing_tables:
        op.create_table(
            "workspace_environment_binding",
            sa.Column("binding_id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("remote_host_id", sa.String(length=36), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("remote_workdir", sa.String(length=512), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("last_invoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("binding_id"),
            sa.ForeignKeyConstraint(["remote_host_id"], ["remote_host.remote_host_id"]),
        )
    _binding_idx = _indexes("workspace_environment_binding")
    if "ix_binding_workspace" not in _binding_idx:
        op.create_index("ix_binding_workspace", "workspace_environment_binding", ["workspace_id"])
    # Partial unique index: at most one default binding per workspace, while any
    # number of non-default bindings coexist. SQLite supports partial indexes.
    if "uq_binding_workspace_default" not in _binding_idx:
        op.create_index(
            "uq_binding_workspace_default", "workspace_environment_binding",
            ["workspace_id"], unique=True,
            sqlite_where=sa.text("is_default = 1"),
        )

    # ── remote_invocation ─────────────────────────────────────────────────
    if "remote_invocation" not in existing_tables:
        op.create_table(
            "remote_invocation",
            sa.Column("invocation_id", sa.String(length=36), nullable=False),
            sa.Column("binding_id", sa.String(length=36), nullable=True),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("trigger", sa.String(length=20), nullable=False, server_default="user"),
            sa.Column("provider", sa.String(length=30), nullable=False, server_default="remote_ssh"),
            sa.Column("command_digest", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("exit_code", sa.Integer(), nullable=False, server_default="-1"),
            sa.Column("risk_level", sa.String(length=10), nullable=False, server_default="L1"),
            sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="success"),
            sa.Column("stdout_ref", sa.String(length=1024), nullable=True),
            sa.Column("stderr_ref", sa.String(length=1024), nullable=True),
            sa.Column("artifact_ref", sa.String(length=1024), nullable=True),
            sa.Column("trace_ref", sa.String(length=255), nullable=True),
            sa.Column("audit_ref", sa.String(length=255), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("invocation_id"),
            sa.ForeignKeyConstraint(
                ["binding_id"], ["workspace_environment_binding.binding_id"]
            ),
        )
    if "ix_invocation_workspace" not in _indexes("remote_invocation"):
        op.create_index("ix_invocation_workspace", "remote_invocation", ["workspace_id"])

    # ── remote_host extension ──────────────────────────────────────────────
    _rh_cols = _columns("remote_host")
    if "environment_tags" not in _rh_cols:
        op.add_column(
            "remote_host",
            sa.Column("environment_tags", sa.Text(), nullable=True),
        )
    if "driver" not in _rh_cols:
        op.add_column(
            "remote_host",
            sa.Column("driver", sa.String(length=30), nullable=False, server_default="ssh"),
        )


def downgrade() -> None:
    op.drop_column("remote_host", "driver")
    op.drop_column("remote_host", "environment_tags")
    op.drop_index("ix_invocation_workspace", table_name="remote_invocation")
    op.drop_table("remote_invocation")
    op.drop_index("uq_binding_workspace_default", table_name="workspace_environment_binding")
    op.drop_index("ix_binding_workspace", table_name="workspace_environment_binding")
    op.drop_table("workspace_environment_binding")
