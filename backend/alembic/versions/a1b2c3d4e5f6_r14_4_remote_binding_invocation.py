"""R14-4: workspace_environment_binding + remote_invocation + remote_host extension

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── workspace_environment_binding ──────────────────────────────────────
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
    op.create_index("ix_binding_workspace", "workspace_environment_binding", ["workspace_id"])
    op.create_unique_constraint(
        "uq_binding_workspace_default", "workspace_environment_binding",
        ["workspace_id", "is_default"],
    )

    # ── remote_invocation ─────────────────────────────────────────────────
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
    op.create_index("ix_invocation_workspace", "remote_invocation", ["workspace_id"])

    # ── remote_host extension ──────────────────────────────────────────────
    op.add_column(
        "remote_host",
        sa.Column("environment_tags", sa.Text(), nullable=True),
    )
    op.add_column(
        "remote_host",
        sa.Column("driver", sa.String(length=30), nullable=False, server_default="ssh"),
    )


def downgrade() -> None:
    op.drop_column("remote_host", "driver")
    op.drop_column("remote_host", "environment_tags")
    op.drop_index("ix_invocation_workspace", table_name="remote_invocation")
    op.drop_table("remote_invocation")
    op.drop_constraint("uq_binding_workspace_default", "workspace_environment_binding")
    op.drop_index("ix_binding_workspace", table_name="workspace_environment_binding")
    op.drop_table("workspace_environment_binding")
