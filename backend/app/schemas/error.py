"""Error schema — Problem Details (RFC 9457) response model."""

from typing import Optional
from pydantic import BaseModel, Field


class ProblemDetail(BaseModel):
    """RFC 9457 Problem Details error response."""
    request_id: str = ""
    status: str = "error"
    error_code: str = "internal_error"
    error_message: str = ""
    error_details_ref: Optional[str] = None
    trace_ref: Optional[str] = None
    gate_ref: Optional[str] = None
    retryable: bool = False
    next_actions: list[str] = Field(default_factory=list)
    created_at: str = ""
