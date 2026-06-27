"""CodingAgentConfig model — external AI coding agent configuration.

D-077 (2026-06-26): Defines the config structure for external AI coding agents
(OpenCode, qcode, custom OpenAI-compat, etc.).

Credential keys are NOT stored in this table. They reference the Credential table
via credential_ref (D-075 BYOK encryption).
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.models.base import Base


class CodingAgentType(str, enum.Enum):
    opencode_cli = "opencode_cli"
    qcode_cli = "qcode_cli"
    openai_compat = "openai_compat"
    platform_agent = "platform_agent"


class CodingAgentInvokeMode(str, enum.Enum):
    cli = "cli"
    api = "api"
    mcp = "mcp"


class CodingAgentStatus(str, enum.Enum):
    configured = "configured"
    connected = "connected"
    error = "error"
    not_configured = "not_configured"


class CodingAgentConfig(Base):
    """Configuration for an external AI coding agent.

    Supports opencode_cli, qcode_cli, openai_compat (custom endpoint), and
    platform_agent (the default — no real config needed).

    D-077: credential_ref points to the Credential table (BYOK encrypted).
    Key/Token must NEVER be stored in the config JSON field.
    """

    __tablename__ = "coding_agent_config"

    agent_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    agent_type: Mapped[CodingAgentType] = mapped_column(
        SAEnum(CodingAgentType), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    invoke_mode: Mapped[CodingAgentInvokeMode] = mapped_column(
        SAEnum(CodingAgentInvokeMode), default=CodingAgentInvokeMode.cli
    )
    # Non-sensitive config: model name, endpoint URL, context_policy, output_policy
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # References Credential table — Key/Token must NOT be in config above
    credential_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[CodingAgentStatus] = mapped_column(
        SAEnum(CodingAgentStatus), default=CodingAgentStatus.not_configured
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
