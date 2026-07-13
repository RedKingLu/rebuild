"""Stage/P0-P6 domain schemas."""

from typing import Optional
from pydantic import BaseModel, Field
from app.schemas.common import GraphPlaceholderFields


class StageResponse(GraphPlaceholderFields):
    stage_code: str  # P0-P6
    stage_name: str
    stage_status: str
    entry_gate_ref: Optional[str] = None
    exit_gate_ref: Optional[str] = None
    stage_plan_ref: Optional[str] = None
    task_plan_refs: list[str] = Field(default_factory=list)
    task_graph_ref: Optional[str] = None
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_gap_refs: list[str] = Field(default_factory=list)
    trace_refs: list[str] = Field(default_factory=list)
    audit_refs: list[str] = Field(default_factory=list)
    blocked_reason: Optional[str] = None

    source_status: str = "not_connected"


class StagePlanRequest(BaseModel):
    plan_summary: str = ""
    plan_detail: dict = Field(default_factory=dict)


class PromotionRequest(BaseModel):
    target_stage: str = Field(..., description="Target P stage, e.g. P3")


class PromotionDecision(BaseModel):
    decision: str = Field(..., description="approve/reject/request_changes")
    reason: str = ""
