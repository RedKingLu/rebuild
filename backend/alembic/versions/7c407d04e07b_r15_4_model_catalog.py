"""R15-4 (C8): model_catalog persistent table

Revision ID: 7c407d04e07b
Revises: c4f1a9d7e2b8
Create Date: 2026-07-10

Adds the model_catalog table used by the ModelsPage catalog tab (R15-4-C8) and as
the FK target for AgentModelEvalResult (C10). Coexists with the runtime ModelProfile /
ModelGateway structures (does not replace them).

Idempotent: the app calls Base.metadata.create_all() on boot (app/core/database.py),
so on a live DB the table may already exist. This migration only creates it when
absent, applying cleanly either way.
"""
from alembic import op
import sqlalchemy as sa

revision = "7c407d04e07b"
down_revision = "c4f1a9d7e2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())
    if "model_catalog" in existing_tables:
        return  # create_all already built it on boot

    op.create_table(
        "model_catalog",
        sa.Column("catalog_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("family", sa.String(length=128), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("input_modalities", sa.JSON(), nullable=True),
        sa.Column("output_modalities", sa.JSON(), nullable=True),
        sa.Column("capability_tags", sa.JSON(), nullable=True),
        sa.Column("task_tags", sa.JSON(), nullable=True),
        sa.Column("license", sa.String(length=64), nullable=True),
        sa.Column("availability_status", sa.String(length=32), nullable=True),
        sa.Column("official_icon_url", sa.String(length=1000), nullable=True),
        sa.Column("official_url", sa.String(length=1000), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("pricing_input", sa.String(length=32), nullable=True),
        sa.Column("pricing_output", sa.String(length=32), nullable=True),
        sa.Column("speed_level", sa.String(length=32), nullable=True),
        sa.Column("latency_level", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("catalog_id", name="pk_model_catalog"),
        sa.UniqueConstraint("model_id", "provider_id", name="uq_model_catalog_model_provider"),
    )
    op.create_index("ix_model_catalog_provider", "model_catalog", ["provider_id"])
    op.create_index("ix_model_catalog_family", "model_catalog", ["family"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "model_catalog" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_model_catalog_family", table_name="model_catalog")
    op.drop_index("ix_model_catalog_provider", table_name="model_catalog")
    op.drop_table("model_catalog")
