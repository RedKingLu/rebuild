"""B-ACC-GATE-APPROVAL-NOT-BOUND: p_gate.action_fingerprint 绑定被审阅的入参

Revision ID: b5d7f9a1c3e5
Revises: a2b4c6d8e0f2
Create Date: 2026-09-15

Adds 1 nullable String(64) column to p_gate:
  - action_fingerprint : str | None  (sha256 hex of "工具名\\x00规范化入参 JSON"；
                        只对 action_approval 这类"动作审批" Gate 有值，
                        stage_promotion 等 Gate 恒为 NULL)

为什么需要它：`_resolve_action_gate` 原先只按 `(run_id, 工具名)` 匹配已批准 Gate
（`tool_name in f"{reason} {summary}"`），内容盲 ⇒ 用户看到入参 X 并批准，该授权挂账
到本 run 内下一次同名工具调用被消费，而那次的入参是当时新传入的 Y ⇒ **人工签核的是
X，实际授权的是 Y**。存指纹后，比对不一致即不授权、重开新 Gate。

存的是不可逆 hex 摘要，不是入参原文 ⇒ 不新增凭据落盘面（入参原文的展示仍走既有的
`_redacted_action_payload` 脱敏渲染）。

revision id 不与既有 32 个 id 冲突（施工前实跑防撞：
  grep -l 'revision = "b5d7f9a1c3e5"' alembic/versions/*.py  →  0 hits；
  且既有清单中无任何 id 以 "b5" 开头，故短前缀匹配亦不歧义）。

Idempotent: the app calls Base.metadata.create_all() on boot, so on a live DB the
column may already exist. This migration only adds it if absent (mirrors the
a2b4c6d8e0f2 / f8a1b2c3d4e5 / e4f5a6b7c8d9 pattern).

不用 batch_alter_table：SQLite 的 ADD COLUMN 原生支持（O(1) 元数据操作，不重写表），
加 batch 会触发表重建 = 无谓风险（沿用 a2b4c6d8e0f2 的取舍）。
"""
from alembic import op
import sqlalchemy as sa

revision = "b5d7f9a1c3e5"
down_revision = "a2b4c6d8e0f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "p_gate" not in set(insp.get_table_names()):
        return  # fresh DB → create_all will build it fully
    existing = {c["name"] for c in insp.get_columns("p_gate")}
    if "action_fingerprint" not in existing:
        op.add_column(
            "p_gate",
            sa.Column("action_fingerprint", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "p_gate" not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns("p_gate")}
    if "action_fingerprint" in existing:
        op.drop_column("p_gate", "action_fingerprint")
