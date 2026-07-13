"""Run domain schemas."""

from typing import Optional, Literal
from pydantic import BaseModel, Field
from app.schemas.common import Meta, GraphPlaceholderFields


class RunCreate(BaseModel):
    run_goal: str = Field(..., min_length=1)
    mode: Literal["auto", "plan", "manual"] = "plan"
    profile_id: Optional[str] = None


class RunResponse(GraphPlaceholderFields):
    run_id: str
    project_id: str
    run_goal: str
    run_status: str
    current_stage: Optional[str] = None
    execution_mode: str = "plan"  # R9-3B
    started_at: str = ""
    updated_at: str = ""
    active_gate: Optional[str] = None
    can_pause: bool = False
    can_cancel: bool = False
    can_resume: bool = False
    stage_status: dict[str, str] = Field(default_factory=dict)

    source_status: str = "not_connected"
    capability_status: str = "not_connected"


class RunResumeRequest(BaseModel):
    decision: str = Field(..., description="approve/modify/pause/reject")
    instruction: Optional[str] = None


class RunListResponse(BaseModel):
    runs: list[RunResponse] = Field(default_factory=list)
    total: int = 0
    meta: Meta = Field(default_factory=Meta)
