"""Gate, Policy Check, and Risk Assessment domain schemas."""

from typing import Optional
from pydantic import BaseModel, Field
from app.schemas.common import GraphPlaceholderFields


class GateResponse(GraphPlaceholderFields):
    gate_id: str
    gate_type: str
    gate_status: str
    project_id: str
    run_id: str
    stage: str
    reason: str = ""
    risk_level: str = "L0"
    summary: str = ""
    options: list[str] = Field(default_factory=list)
    recommended_option: Optional[str] = None
    decision: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    trace_refs: list[str] = Field(default_factory=list)
    audit_ref: Optional[str] = None
    source_status: str = "not_connected"


class GateDecisionRequest(BaseModel):
    decision: str = Field(..., description="approve/reject/request_changes/pause/cancel")
    reason: str = ""
    accepted_risk: Optional[str] = None


class PolicyCheckRequest(BaseModel):
    action_type: str = ""
    risk_level: str = "L0"
    context: dict = Field(default_factory=dict)


class PolicyCheckResponse(BaseModel):
    check_id: str
    allowed: bool = True
    reason: str = ""
    required_gate: bool = False
    # real: policy_check derives from mode_policy.authorize_action (R10 T12)
    source_status: str = "real"


class RiskAssessmentRequest(BaseModel):
    action_type: str = ""
    target: str = ""
    context: dict = Field(default_factory=dict)


class RiskAssessmentResponse(BaseModel):
    assessment_id: str
    risk_level: str = "L0"
    summary: str = ""
    mitigations: list[str] = Field(default_factory=list)
    # real: risk_assess derives from mode_policy.authorize_action (R10 T12)
    source_status: str = "real"


class GateListResponse(BaseModel):
    gates: list[GateResponse] = Field(default_factory=list)
    total: int = 0
