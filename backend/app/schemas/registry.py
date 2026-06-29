"""Pydantic schemas for Resource Registry (replaces resource.py placeholder)."""

from datetime import datetime
from pydantic import BaseModel, Field


class ResourceCreate(BaseModel):
    resource_type: str = Field(...)
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    version: str = "1.0.0"
    source_type: str = "internal_current"
    source_trust_level: str = "trusted_current"
    source_path_or_ref: str | None = None
    risk_level: str = "L0"
    status: str = "draft"
    permission_scope: str = "read_only"
    capabilities: dict | None = None
    allowed_actions: list | None = None
    blocked_actions: list | None = None
    input_contract: str = ""
    output_contract: str = ""
    artifact_evidence_contract: str = ""
    gate_policy: str = ""
    trace_policy: str = "required"
    audit_policy: str = "conditional"
    credential_ref: str | None = None
    review_status: str = "not_reviewed"
    type_metadata: dict | None = None
    enabled: bool = True


class ResourceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    version: str | None = None
    source_type: str | None = None
    source_trust_level: str | None = None
    risk_level: str | None = None
    status: str | None = None
    permission_scope: str | None = None
    capabilities: dict | None = None
    allowed_actions: list | None = None
    blocked_actions: list | None = None
    input_contract: str | None = None
    output_contract: str | None = None
    artifact_evidence_contract: str | None = None
    gate_policy: str | None = None
    trace_policy: str | None = None
    audit_policy: str | None = None
    credential_ref: str | None = None
    review_status: str | None = None
    review_notes: str | None = None
    type_metadata: dict | None = None
    superseded_by: str | None = None
    enabled: bool | None = None


class ResourceResponse(BaseModel):
    resource_id: str
    resource_type: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    source_type: str = "internal_current"
    source_trust_level: str = "trusted_current"
    source_path_or_ref: str | None = None
    risk_level: str = "L0"
    status: str = "draft"
    permission_scope: str = "read_only"
    capabilities: dict | None = None
    allowed_actions: list | None = None
    blocked_actions: list | None = None
    input_contract: str = ""
    output_contract: str = ""
    artifact_evidence_contract: str = ""
    gate_policy: str = ""
    trace_policy: str = "required"
    audit_policy: str = "conditional"
    credential_ref: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    review_status: str = "not_reviewed"
    reviewer: str | None = None
    review_notes: str = ""
    type_metadata: dict | None = None
    source_status: str = "real"
    capability_status: str = "active"
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ResourceListData(BaseModel):
    resources: list[ResourceResponse]
    total: int
    source_status: str = "real"
    capability_status: str = "active"


class RegistrySummary(BaseModel):
    by_type: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_risk_level: dict[str, int] = Field(default_factory=dict)
    total: int = 0
    enabled_count: int = 0
    schedulable_count: int = 0
    source_status: str = "real"
