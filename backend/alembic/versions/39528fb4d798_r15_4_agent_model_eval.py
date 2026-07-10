"""R15-4 (C10): agent_model_eval table

Revision ID: 39528fb4d798
Revises: 7c407d04e07b
Create Date: 2026-07-10

Adds agent_model_eval — imported Agent×Model evaluation results (display-only; not an
auto-evaluation engine). Idempotent: only creates the table when absent (the app calls
Base.metadata.create_all() on boot).
"""
from alembic import op
import sqlalchemy as sa

revision = "39528fb4d798"
down_revision = "7c407d04e07b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "agent_model_eval" in set(insp.get_table_names()):
        return
    op.create_table(
        "agent_model_eval",
        sa.Column("eval_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("catalog_id", sa.String(length=64), nullable=True),
        sa.Column("agent_type", sa.String(length=64), nullable=True),
        sa.Column("task_type", sa.String(length=64), nullable=True),
        sa.Column("scenario", sa.String(length=255), nullable=True),
        sa.Column("metric", sa.String(length=64), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("cost_level", sa.String(length=32), nullable=True),
        sa.Column("latency_level", sa.String(length=32), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("eval_method", sa.Text(), nullable=True),
        sa.Column("eval_version", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("eval_id", name="pk_agent_model_eval"),
    )
    op.create_index("ix_agent_model_eval_model", "agent_model_eval", ["model_id"])
    op.create_index("ix_agent_model_eval_task", "agent_model_eval", ["task_type"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "agent_model_eval" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_agent_model_eval_task", table_name="agent_model_eval")
    op.drop_index("ix_agent_model_eval_model", table_name="agent_model_eval")
    op.drop_table("agent_model_eval")
