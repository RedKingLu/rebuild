"""Agent Definition model — structured contract for Agent types."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class AgentType(str, enum.Enum):
    node_worker = "node_worker"
    acceptance = "acceptance"
    auto_review = "auto_review"
    expert = "expert"
    conversation_gate = "conversation_gate"


class AgentCategory(str, enum.Enum):
    """Agent 分类：系统内置 vs 专家（用户自定义/导入）。"""
    system = "system"      # 系统内置，不可删除，不可调整 prompt
    expert = "expert"      # 专家 Agent，用户自定义或导入，可全配置


class DefinitionStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    deprecated = "deprecated"
    disabled = "disabled"


class AgentDefinition(Base):
    __tablename__ = "agent_definition"

    agent_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    agent_type: Mapped[AgentType] = mapped_column(SAEnum(AgentType), nullable=False)
    category: Mapped[AgentCategory] = mapped_column(
        SAEnum(AgentCategory), default=AgentCategory.expert, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), default="1.0.0")
    status: Mapped[DefinitionStatus] = mapped_column(
        SAEnum(DefinitionStatus), default=DefinitionStatus.draft, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(default=True)  # 专家 Agent 启用/禁用开关

    # Contract 13 elements (from D-018)
    responsibilities: Mapped[str] = mapped_column(Text, default="")
    forbidden: Mapped[str] = mapped_column(Text, default="")
    context_recipe_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    memory_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gate_rules: Mapped[str] = mapped_column(Text, default="")
    failure_escalation: Mapped[str] = mapped_column(Text, default="")
    tool_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    skill_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mcp_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_output_contract: Mapped[str] = mapped_column(Text, default="")
    artifact_evidence_contract: Mapped[str] = mapped_column(Text, default="")
    self_check_acceptance: Mapped[str] = mapped_column(Text, default="")

    # Context Recipe (JSON object)
    context_recipe: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Memory Policy (JSON object)
    memory_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Custom prompt (专家 Agent 可自定义系统提示词)
    custom_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 绑定的 Skill/Tool/MCP 列表（JSON 数组，存储引用 ID）
    bound_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    bound_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    bound_mcps: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ModelGateway reference (no Key)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 来源（local / community_import / user_created）
    origin: Mapped[str] = mapped_column(String(50), default="user_created")

    # Source markers
    source_status: Mapped[str] = mapped_column(String(50), default="real")
    capability_status: Mapped[str] = mapped_column(String(50), default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
