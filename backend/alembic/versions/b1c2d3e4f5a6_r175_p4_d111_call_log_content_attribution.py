"""R17.5-P4-FIX 批4 (D-111): call_log 内容列 + 归因列（可观测性）

Revision ID: b1c2d3e4f5a6
Revises: a1c2e3f40901
Create Date: 2026-07-24

Adds 6 nullable columns to call_log so每条 LLM 调用可观测其内容并归因到项目/阶段/运行
（D-111，根因在持久化层：原表只存 token/模型/状态元数据）：
  - project_id        : str | None   归因——所属项目
  - stage             : str | None   归因——所属 P 阶段（p0..p6）
  - run_id            : str | None   归因——所属运行
  - request_messages  : Text | None  脱敏后的请求 messages（JSON 文本，D-032 脱敏 + 截断控体积）
  - response_content  : Text | None  脱敏后的完成文本
  - content_truncated : Integer      内容是否被截断（SQLite bool as int）

Idempotent: the app calls Base.metadata.create_all() on boot, so on a live DB the
columns may already exist. This migration只在缺列时 add_column（mirrors a1c2e3f40901 pattern）。
revision-id 不与既有 head 冲突（链于 a1c2e3f40901）。
"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "a1c2e3f40901"
branch_labels = None
depends_on = None


_NEW_COLUMNS = [
    ("project_id", sa.Column("project_id", sa.String(36), nullable=True)),
    ("stage", sa.Column("stage", sa.String(20), nullable=True)),
    ("run_id", sa.Column("run_id", sa.String(36), nullable=True)),
    ("request_messages", sa.Column("request_messages", sa.Text(), nullable=True)),
    ("response_content", sa.Column("response_content", sa.Text(), nullable=True)),
    ("content_truncated", sa.Column("content_truncated", sa.Integer(), nullable=True)),
]
_NEW_INDEXES = [
    ("ix_call_log_project_id", "project_id"),
    ("ix_call_log_stage", "stage"),
    ("ix_call_log_run_id", "run_id"),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "call_log" not in set(insp.get_table_names()):
        return  # fresh DB → create_all will build it fully
    existing = {c["name"] for c in insp.get_columns("call_log")}
    for name, col in _NEW_COLUMNS:
        if name not in existing:
            op.add_column("call_log", col)
    existing_idx = {i["name"] for i in insp.get_indexes("call_log")}
    for idx_name, col_name in _NEW_INDEXES:
        if idx_name not in existing_idx:
            op.create_index(idx_name, "call_log", [col_name])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "call_log" not in set(insp.get_table_names()):
        return
    existing_idx = {i["name"] for i in insp.get_indexes("call_log")}
    for idx_name, _ in _NEW_INDEXES:
        if idx_name in existing_idx:
            op.drop_index(idx_name, "call_log")
    existing = {c["name"] for c in insp.get_columns("call_log")}
    for name, _ in _NEW_COLUMNS:
        if name in existing:
            op.drop_column("call_log", name)
