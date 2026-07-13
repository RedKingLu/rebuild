"""Common schema components — request/response envelope, meta, capability fields.

All rebuild API responses use a unified envelope with source_status and capability_status
metadata so the frontend can accurately display real vs mock capability.
"""

from datetime import datetime, timezone
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Meta(BaseModel):
    """Response metadata — capability and source status.

    WP-4 F-4: Removed R4 placeholder defaults (mock/not_connected/volatile).
    Real backend: source_status=real, capability_status=available, persistence=durable.
    Endpoints that are NOT yet real should explicitly set the appropriate status,
    not rely on defaults that claim to be real.
    """
    source_status: str = "real"
    capability_status: str = "available"
    not_connected_reason: str = ""
    generated_at: str = Field(default_factory=_now)
    persistence: str = "durable"
    detail: str = ""


class GraphPlaceholderFields(BaseModel):
    """R4 placeholder fields for future LangGraph integration."""
    checkpoint_ref: Optional[str] = None
    interrupt_ref: Optional[str] = None
    resume_ref: Optional[str] = None
    graph_capability_status: str = "not_connected"
    transition_mode: str = "unknown"


class SuccessEnvelope(BaseModel):
    """Unified success response envelope."""
    request_id: str = ""
    status: str = "success"
    data: Any = None
    meta: Meta = Field(default_factory=Meta)
    trace_ref: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    """Error detail — one item in a Problem Details response."""
    request_id: str = ""
    status: str = "error"
    error_code: str = "internal_error"
    error_message: str = ""
    error_details_ref: Optional[str] = None
    trace_ref: Optional[str] = None
    gate_ref: Optional[str] = None
    retryable: bool = False
    next_actions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class ListMeta(BaseModel):
    """Pagination metadata for list responses."""
    total: int = 0
    limit: int = 50
    offset: int = 0
