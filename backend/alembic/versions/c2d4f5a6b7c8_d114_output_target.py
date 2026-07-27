"""D-114: task_plan.output_target + task_node.output_target for 脚手架先行/单一可构建工程

Revision ID: c2d4f5a6b7c8
Revises: b1c2d3e4f5a6
Create Date: 2026-07-26

Adds 1 nullable string column to each of task_plan / task_node（D-114 方案A 脚手架先行）：
  - output_target : str | None  （目标产出路径，目标工程相对路径，如 output_code/{Project}/Services/）

P4 worker 据 task_node.output_target 把产物写入同一目标工程树（output_code/{Project}/{层}/），
实现 R17.3-1 §8 P4-4/P4-10 的单一可构建目标工程；缺失时回退 output_code/{node_id}/（向后兼容）。

Idempotent: app boots with Base.metadata.create_all(); on a live DB the column may already
exist. Only adds if absent. revision-id 链于 b1c2d3e4f5a6（D-111 head），不冲突。
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d4f5a6b7c8"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def _add_if_absent(insp, table: str, column: str) -> None:
    if table not in set(insp.get_table_names()):
        return  # fresh DB → create_all builds it fully
    existing = {c["name"] for c in insp.get_columns(table)}
    if column not in existing:
        op.add_column(table, sa.Column(column, sa.String(length=512), nullable=True))


def _drop_if_present(insp, table: str, column: str) -> None:
    if table not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if column in existing:
        op.drop_column(table, column)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    _add_if_absent(insp, "task_plan", "output_target")
    _add_if_absent(insp, "task_node", "output_target")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    _drop_if_present(insp, "task_node", "output_target")
    _drop_if_present(insp, "task_plan", "output_target")
