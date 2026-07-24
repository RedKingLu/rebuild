"""CallLog model — persistent model call records (FB-006).

Replaces the in-memory volatile call log with DB-persisted records.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CallLog(Base):
    __tablename__ = "call_log"

    model_call_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(String(200), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(100), default="system-default")
    selected_model: Mapped[str] = mapped_column(String(200), nullable=False)
    selection_reason: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # completed / failed / blocked
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    error_category: Mapped[str] = mapped_column(String(50), default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    fallback_used: Mapped[bool] = mapped_column(Integer, default=0)  # SQLite bool as int
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(50), default="api")  # api / self_test / platform_assistant
    # ── R13-4: 3 fusion sub-call-tree columns (additive; nullable for plain api calls) ──
    fusion_parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    call_type: Mapped[str] = mapped_column(String(32), default="api", index=True)
    # api / fusion_panel / fusion_judge / fusion_synthesizer / self_moa_sample / self_test / platform_assistant
    fusion_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # ── D-111: 归因列（可空，向后兼容旧行）——把每条调用归到某项目/阶段/运行 ──
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # ── D-111: 内容列（脱敏 D-032 + 截断控体积；可空）——模型界面据此显示每条调用内容 ──
    request_messages: Mapped[str | None] = mapped_column(Text, nullable=True)   # 脱敏后的请求 messages（JSON 文本）
    response_content: Mapped[str | None] = mapped_column(Text, nullable=True)   # 脱敏后的完成文本
    content_truncated: Mapped[bool] = mapped_column(Integer, default=0)         # 内容是否被截断（SQLite bool as int）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "model_call_id": self.model_call_id,
            "provider_id": self.provider_id,
            "profile_id": self.profile_id,
            "strategy_id": self.strategy_id,
            "selected_model": self.selected_model,
            "selection_reason": self.selection_reason,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error_category": self.error_category,
            "retry_count": self.retry_count,
            "fallback_used": bool(self.fallback_used),
            "usage_summary": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
            "source": self.source,
            "fusion_parent_id": self.fusion_parent_id,
            "call_type": self.call_type,
            "fusion_run_id": self.fusion_run_id,
            "project_id": self.project_id,
            "stage": self.stage,
            "run_id": self.run_id,
            "request_messages": self.request_messages,
            "response_content": self.response_content,
            "content_truncated": bool(self.content_truncated),
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "completed_at": self.completed_at.isoformat() if self.completed_at else "",
        }
