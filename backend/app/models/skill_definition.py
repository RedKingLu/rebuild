"""Skill Definition model — R/P series isolation."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class SkillSeries(str, enum.Enum):
    R = "R"  # R-series: rebuild construction
    P = "P"  # P-series: platform runtime


class SkillCategory(str, enum.Enum):
    common = "common"          # 通用（跨阶段）
    p0 = "p0"                  # P0 接入
    p1 = "p1"                  # P1 建档
    p2 = "p2"                  # P2 评估
    p3 = "p3"                  # P3 规划
    p4 = "p4"                  # P4 执行
    p5 = "p5"                  # P5 验证
    p6 = "p6"                  # P6 交付
    other = "other"            # 其他


class SkillStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    planned = "planned"
    platform_runtime = "platform_runtime"
    not_active_in_local_agent = "not_active_in_local_agent"
    deprecated = "deprecated"
    disabled = "disabled"


class SkillDefinition(Base):
    __tablename__ = "skill_definition"

    skill_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), default="1.0.0")
    series: Mapped[SkillSeries] = mapped_column(SAEnum(SkillSeries), nullable=False)
    category: Mapped[SkillCategory] = mapped_column(SAEnum(SkillCategory), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    required_model_policy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    required_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    required_context: Mapped[list | None] = mapped_column(JSON, nullable=True)

    status: Mapped[SkillStatus] = mapped_column(
        SAEnum(SkillStatus), default=SkillStatus.draft, nullable=False
    )

    # Source markers
    skill_source: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # "local_cc" / "ecc" / "rebuild_self"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_status: Mapped[str] = mapped_column(String(50), default="real")
    capability_status: Mapped[str] = mapped_column(String(50), default="active")

    # R-series only
    directory_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
