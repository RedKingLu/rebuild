"""Conversation + ChatMessage models — UX-3: persistent agent conversation sessions.

A conversation is one continuous dialogue with the specialist agent for a given
(project, run, stage, agent_role). New conversations are started on stage change or
when entering acceptance/rework (see conversation_service.agent_role_for).
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "p_conversation"

    conversation_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: _new_id("conv")
    )
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), default="")
    stage: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    # agent_role = AgentType value of the specialist this convo is with
    # (node_worker | acceptance | auto_review | expert | conversation_gate)
    agent_role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (
        Index("ix_conversation_project_active", "project_id", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "stage": self.stage,
            "agent_role": self.agent_role,
            "title": self.title,
            "status": self.status,
            "message_count": self.message_count,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else "",
        }


class ChatMessage(Base):
    __tablename__ = "p_chat_message"

    message_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: _new_id("msg")
    )
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | agent | system | tool
    content: Mapped[str] = mapped_column(Text, default="")
    # meta: optional structured payload — tool name, gate_id, token usage, etc.
    meta: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    __table_args__ = (
        Index("ix_message_conv_created", "conversation_id", "created_at"),
    )

    def to_dict(self) -> dict:
        meta_obj = {}
        if self.meta:
            import json
            try:
                meta_obj = json.loads(self.meta)
            except Exception:
                meta_obj = {}
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "meta": meta_obj,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }
