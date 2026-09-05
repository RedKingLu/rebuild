"""Project model — persisted project with source configuration."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class ProjectStatus(str, enum.Enum):
    created = "created"
    active = "active"
    running = "running"
    archived = "archived"


class SourceType(str, enum.Enum):
    local_dir = "local_dir"
    git = "git"
    zip_source = "zip"
    github = "github"
    manual = "manual"

    @classmethod
    def _missing_(cls, value):
        """容错：反序列化时遇到 DB 中历史非法值（如 'local'）不抛 LookupError。

        验收报告 R17-1B V-R17-1B-1/§11.P0-1：/source/projects 曾因
        r1216 遗留 source_type='local' 导致 SQLAlchemy 枚举反序列化 LookupError
        → 整表 500。改为返_UNKNOWN占位，由 migration 任务清洗。
        """
        return None  # 返回 None → nullable 列；如列nullable=False则走 default

    @classmethod
    def coerce(cls, value):
        """从字符串安全取枚举；非法值返 None（不抛）。用于 IO 边界（DB读/ API 入参）。"""
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None


class Project(Base):
    __tablename__ = "project"

    project_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus), default=ProjectStatus.created, nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType), default=SourceType.local_dir, nullable=False
    )
    source_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # source_config examples:
    #   {"path": "/home/user/project"}  for local_dir
    #   {"git_host_id": "uuid", "branch": "main", "subpath": "/"}  for git
    #   {} for manual
    current_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_gate: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence_gap_count: Mapped[int] = mapped_column(default=0)
    workspace_status: Mapped[str | None] = mapped_column(String(50), default="ready")
    onboarding_done: Mapped[bool] = mapped_column(default=False)
    coding_agent_ref: Mapped[str | None] = mapped_column(
        String(36), nullable=True, default=None
    )  # D-078/R9-3A: FK → coding_agent_config.agent_id; default None = platform_agent
    # D-088 / R9-5-5: delegation scope — how much of the P0-P6 flow is delegated to
    # an external platform (e.g. OpenCode).  Paired with coding_agent_ref.
    #   none         → self-hosted platform handles everything (default)
    #   coding_only  → external platform handles P4 coding tasks only
    #   all_stages   → external platform handles all P0-P6 stages
    external_platform_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    # D-098: model strategy mode for this project.
    #   global_unified → all calls use global_model_ref (one model for everything)
    #   custom         → per-agent model (AgentDefinition.model_policy_ref), else system default
    # 默认 global_unified + null ref ⇒ 回落系统默认策略（动态 user_strategies.yaml override），不改现状。
    model_strategy_mode: Mapped[str] = mapped_column(String(32), default="global_unified", server_default="global_unified")
    global_model_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    # R17.5 WP-6 (Q-R17.4-3-2): 目标运行环境约束（用户在引导中点选的目标 CPU 架构 + 目标 OS）。
    # 硬约束（可经用户 Gate 改，非永久冻结）；作为 P0-P6 各阶段目标锚点输入之一。
    # 结构（开放、可扩展、非封闭枚举，§2.3 反规则引擎）：
    #   {"cpu_arch": str|null, "cpu_arch_label": str, "os": str|null, "os_label": str,
    #    "source": "user_onboarding", "note": str}
    # 这是【用户输入采集】而非识别逻辑（识别归 LLM）；样本值随项目由用户点选/输入，不硬编码枚举。
    migration_target: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    # R17.5-P4-FIX 批3 (D-109): 技术路线选型红线（与 migration_target 同级 CPU/OS 红线）。
    # LLM 在 P1→P2 gate 基于 P0/P1 识别事实 + migration_target 产出「技术选型建议」（目标语言/
    # 运行时/数据库/Web 框架/中间件替换/关键架构决策，每项含 推荐+理由+备选），用户裁决批准后
    # 落此字段成为项目红线，贯穿注入 P2 规划 / P4 执行并被验收校验。结构（开放可扩展，非封闭枚举）：
    #   {"target_language": {recommendation, reasoning, alternatives[]}, "runtime": {...},
    #    "database": {...}, "web_framework": {...}, "middleware_replacements": [{...}],
    #    "key_arch_decisions": [{...}], "status": "approved", "decided_at": str, "source": str}
    # 选型由 LLM 基于真实源码事实推荐、用户拍板；样本值随项目生成，不硬编码维度枚举（§2.3）。
    tech_selection: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    # R20-2-01：项目的【重构场景 id】（自由文本）。
    # ⚠️ 刻意【不用】SAEnum / Python Enum / Literal —— 理由：D-117③ + AGENTS §10-27。
    #   场景体系分「典型场景 + 开放扩展」两层，"包括但不限于"；新增一个场景 = 新增一个
    #   source/skills/scenarios/<id>/ 目录，【零 Python 与前端改动】。做成枚举会使新增场景
    #   必须同时改 Enum + Alembic 迁移 + schema 三处，直接违反该裁决（R20-3-01 判 rework）。
    #   本文件的 source_type（SAEnum）与 schemas/project.py 的 Literal 是【不可照搬的先例】：
    #   那些取值集合由平台封闭定义，而场景的取值集合由【磁盘目录】决定。
    # 取值合法性不由枚举校验，而由 scenario_loader 的【形状白名单】把门
    #   （^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$，见 scenario_loader.py:87，兼路径安全）；
    #   非法 / 目录不存在 → 诚实回落 _generic 并上报 (code, message)，绝不猜场景（R20-2-06）。
    # 长度 64 = 上述形状白名单的字符上限（1 + 63）逐字对齐，不是拍脑袋的数。
    #   注意（如实标注）：当前库为 SQLite，VARCHAR 长度【不在库层强制】；该声明是意图文档
    #   与未来迁 PostgreSQL 时的真实约束，不得对外表述为"长度已在库层校验"。
    # NULL = 用户尚未选择场景（≠ 选了空场景）；空串在写入侧归一为 NULL（project_service.create）。
    scenario: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
