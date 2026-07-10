"""AgentModelEvalResult — Agent×Model 评测结果基础持久化（R15-4-C10）。

DISPLAY-ONLY: this table stores IMPORTED evaluation results; rebuild does NOT run an
automatic evaluation engine (red line #9). Every imported result MUST expose source,
eval_method, limitations, sample_count (red line #10/#11) — enforced at the display
layer. The catalog_id associates to ModelCatalogEntry (C8) when available.

Alembic head at time of writing: 7c407d04e07b (C8). New migration chains from it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentModelEvalResult(Base):
    __tablename__ = "agent_model_eval"

    eval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Optional FK to ModelCatalogEntry.model_id (logical, not enforced hard to keep imports flexible).
    catalog_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    success_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Mandatory display fields (red line #10/#11): every row MUST carry these.
    eval_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_agent_model_eval_model", "model_id"),
        Index("ix_agent_model_eval_task", "task_type"),
    )
