"""ModelCatalog — persistent model catalog table (R15-4-C8).

A durable, queryable model directory that COEXISTS with the runtime ModelProfile /
ModelGateway in-memory structures (provider_registry.py / model_gateway.py). It does
NOT replace the running-time model invocation path — it is a reference catalog for the
ModelsPage catalog tab, reusable by ModelGateway in the future, and关联 AgentModelEvalResult.

Alembic head at time of writing: c4f1a9d7e2b8 (R15-4). New migration chains from it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Integer, JSON, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModelCatalogEntry(Base):
    __tablename__ = "model_catalog"

    # catalog_id is synthetic PK; model_id is the logical model identifier
    # (e.g. "deepseek-v4-pro"), paired with provider_id for uniqueness.
    catalog_id: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    family: Mapped[str] = mapped_column(String(128), default="")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_modalities: Mapped[list | None] = mapped_column(JSON, default=list)
    output_modalities: Mapped[list | None] = mapped_column(JSON, default=list)
    capability_tags: Mapped[list | None] = mapped_column(JSON, default=list)
    task_tags: Mapped[list | None] = mapped_column(JSON, default=list)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    availability_status: Mapped[str] = mapped_column(String(32), default="available")
    official_icon_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    official_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="seed")   # seed|community|imported
    pricing_input: Mapped[str | None] = mapped_column(String(32), nullable=True)   # future
    pricing_output: Mapped[str | None] = mapped_column(String(32), nullable=True)  # future
    speed_level: Mapped[str | None] = mapped_column(String(32), nullable=True)     # future
    latency_level: Mapped[str | None] = mapped_column(String(32), nullable=True)   # future
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("model_id", "provider_id", name="uq_model_catalog_model_provider"),
    )
