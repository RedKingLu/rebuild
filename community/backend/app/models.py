"""Community service data models (independent SQLite).

R15-4-C3. Field set absorbs R15-3 §11 (Open VSX / VS Code Marketplace / HF Hub /
Docker Registry). NOT shared with the platform DB.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, Boolean, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CommunityResource(Base):
    __tablename__ = "community_resource"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    readme: Mapped[str] = mapped_column(Text, default="")            # Markdown 正文
    namespace: Mapped[str] = mapped_column(String(128), default="")
    publisher: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(50), default="1.0.0")
    versions: Mapped[list | None] = mapped_column(JSON, default=list)
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    categories: Mapped[list | None] = mapped_column(JSON, default=list)
    license: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(32), default="community")   # official|community
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    icon_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    files: Mapped[list | None] = mapped_column(JSON, default=list)          # [{name,size,download_url}]
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="")
    package_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # local zip on community server
    dependencies: Mapped[list | None] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommunityDoc(Base):
    """Community online documentation (R16-E1). Read-only, seeded by 发布侧.

    Stored in the independent community SQLite (NOT shared with the platform DB,
    NOT the platform's Knowledge registry). Trusted release-side seed content.
    No body_path indirection — Markdown lives in-column (trusted seed only).
    """
    __tablename__ = "community_doc"

    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    body_markdown: Mapped[str] = mapped_column(Text, default="")   # Markdown 正文
    category: Mapped[str] = mapped_column(String(64), default="")
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    version: Mapped[str] = mapped_column(String(50), default="1.0.0")
    source: Mapped[str] = mapped_column(String(32), default="community")   # official|community
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityNews(Base):
    __tablename__ = "community_news"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)


class CommunityModelEntry(Base):
    __tablename__ = "community_model_entry"

    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    provider_id: Mapped[str] = mapped_column(String(128), default="")
    family: Mapped[str] = mapped_column(String(128), default="")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_modalities: Mapped[list | None] = mapped_column(JSON, default=list)
    output_modalities: Mapped[list | None] = mapped_column(JSON, default=list)
    capability_tags: Mapped[list | None] = mapped_column(JSON, default=list)
    task_tags: Mapped[list | None] = mapped_column(JSON, default=list)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    official_icon_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    official_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    availability_status: Mapped[str] = mapped_column(String(32), default="available")
    source: Mapped[str] = mapped_column(String(32), default="community")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityEvaluation(Base):
    """model-index style eval result (HF absorption). Display only, not an engine."""
    __tablename__ = "community_evaluation"

    eval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(nullable=True)
    success_rate: Mapped[float | None] = mapped_column(nullable=True)
    cost_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eval_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
