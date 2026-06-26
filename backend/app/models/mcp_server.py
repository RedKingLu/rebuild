"""MCP Server model — Model Context Protocol server configuration (Phase 12)."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MCPServer(Base):
    __tablename__ = "mcp_server"

    mcp_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    # Transport: "stdio" (subprocess) or "sse" (HTTP)
    transport: Mapped[str] = mapped_column(String(20), default="stdio", nullable=False)

    # Stdio config
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)  # e.g. "npx"
    args: Mapped[list | None] = mapped_column(JSON, nullable=True)           # e.g. ["-y", "@modelcontextprotocol/server-github"]
    env_vars: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # e.g. {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"}

    # SSE config
    sse_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(20), default="disconnected")  # disconnected / connecting / connected / error
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Discovered tools (populated on connection)
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Metadata
    origin: Mapped[str] = mapped_column(String(50), default="user_created")  # user_created / builtin / community
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
