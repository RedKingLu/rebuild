"""R13-4: FusionProfile 3 tables + call_log 3 fusion columns.

Fusion as a virtual model (方案 E, D-035): 3 independent tables
(fusion_profile / fusion_run / fusion_run_participant) plus call_log extended with
fusion_parent_id / call_type / fusion_run_id to carry sub-call trees.

Merge note: descends from current single head e5f6a7b8c9d0 (R10 T8 plan_delta).

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-07-06
"""

from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── fusion_profile ─────────────────────────────────────────────────
    op.create_table(
        "fusion_profile",
        sa.Column("fusion_profile_id", sa.String(length=36), nullable=False),
        sa.Column("virtual_profile_ref", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("panel_participants", sa.JSON(), nullable=False),
        sa.Column("judge", sa.JSON(), nullable=False),
        sa.Column("synthesizer", sa.JSON(), nullable=False),
        sa.Column("style", sa.String(length=16), server_default="balanced"),
        sa.Column("enabled_stages", sa.JSON(), nullable=True),
        sa.Column("trigger", sa.String(length=16), server_default="manual"),
        sa.Column("cost_limit", sa.Float(), server_default="0.0"),
        sa.Column("timeout_seconds", sa.Integer(), server_default="120"),
        sa.Column("audit_level", sa.String(length=16), server_default="summary"),
        sa.Column("fallback_single_model", sa.String(length=255), nullable=True),
        sa.Column("excluded_providers", sa.JSON(), nullable=True),
        sa.Column("self_moa_enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("max_fusion_depth", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(length=64), server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("fusion_profile_id"),
        sa.UniqueConstraint("virtual_profile_ref", name="uq_fusion_profile_virtual_profile_ref"),
    )

    # ── fusion_run ─────────────────────────────────────────────────────
    op.create_table(
        "fusion_run",
        sa.Column("fusion_run_id", sa.String(length=36), nullable=False),
        sa.Column("fusion_profile_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=8), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="manual"),
        sa.Column("status", sa.String(length=16), server_default="pending"),
        sa.Column("strategy", sa.String(length=24), server_default="panel_judge_synth"),
        sa.Column("winner_ref", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.String(length=8), server_default="medium"),
        sa.Column("degraded", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("degrade_reason", sa.String(length=255), nullable=True),
        sa.Column("cost_sum", sa.Float(), server_default="0.0"),
        sa.Column("latency_sum_ms", sa.Float(), server_default="0.0"),
        sa.Column("trace_refs", sa.JSON(), nullable=True),
        sa.Column("audit_refs", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("fusion_run_id"),
    )
    op.create_index("ix_fusion_run_fusion_profile_id", "fusion_run", ["fusion_profile_id"])
    op.create_index("ix_fusion_run_project_id", "fusion_run", ["project_id"])
    op.create_index("ix_fusion_run_status", "fusion_run", ["status"])

    # ── fusion_run_participant ─────────────────────────────────────────
    op.create_table(
        "fusion_run_participant",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("fusion_run_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("profile_ref", sa.String(length=255), nullable=False),
        sa.Column("provider_id", sa.String(length=100), server_default=""),
        sa.Column("model", sa.String(length=200), server_default=""),
        sa.Column("perspective", sa.String(length=64), server_default=""),
        sa.Column("status", sa.String(length=16), server_default="pending"),
        sa.Column("latency_ms", sa.Float(), server_default="0.0"),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), server_default="0"),
        sa.Column("total_tokens", sa.Integer(), server_default="0"),
        sa.Column("cost", sa.Float(), server_default="0.0"),
        sa.Column("trace_ref", sa.String(length=64), server_default=""),
        sa.Column("model_call_id", sa.String(length=64), nullable=True),
        sa.Column("content_ref", sa.String(length=64), nullable=True),
        sa.Column("error_category", sa.String(length=50), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fusion_run_participant_fusion_run_id", "fusion_run_participant", ["fusion_run_id"])
    op.create_index("ix_fusion_run_participant_model_call_id", "fusion_run_participant", ["model_call_id"])

    # ── call_log: add 3 fusion columns (additive, SQLite-safe) ─────────
    op.add_column("call_log", sa.Column("fusion_parent_id", sa.String(length=36), nullable=True))
    op.add_column("call_log", sa.Column("call_type", sa.String(length=32), server_default="api"))
    op.add_column("call_log", sa.Column("fusion_run_id", sa.String(length=36), nullable=True))
    op.create_index("ix_call_log_fusion_parent_id", "call_log", ["fusion_parent_id"])
    op.create_index("ix_call_log_fusion_run_id", "call_log", ["fusion_run_id"])
    op.create_index("ix_call_log_call_type", "call_log", ["call_type"])


def downgrade() -> None:
    op.drop_index("ix_call_log_call_type", table_name="call_log")
    op.drop_index("ix_call_log_fusion_run_id", table_name="call_log")
    op.drop_index("ix_call_log_fusion_parent_id", table_name="call_log")
    op.drop_column("call_log", "fusion_run_id")
    op.drop_column("call_log", "call_type")
    op.drop_column("call_log", "fusion_parent_id")

    op.drop_index("ix_fusion_run_participant_model_call_id", table_name="fusion_run_participant")
    op.drop_index("ix_fusion_run_participant_fusion_run_id", table_name="fusion_run_participant")
    op.drop_table("fusion_run_participant")

    op.drop_index("ix_fusion_run_status", table_name="fusion_run")
    op.drop_index("ix_fusion_run_project_id", table_name="fusion_run")
    op.drop_index("ix_fusion_run_fusion_profile_id", table_name="fusion_run")
    op.drop_table("fusion_run")

    op.drop_table("fusion_profile")
