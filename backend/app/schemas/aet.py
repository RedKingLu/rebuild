"""Artifact, Evidence, Trace, Audit (AET) domain schemas."""

from typing import Optional
from pydantic import BaseModel, Field


class ArtifactResponse(BaseModel):
    artifact_id: str
    artifact_type: str
    title: str
    stage: str = ""
    artifact_status: str
    is_evidence_candidate: bool = False
    content_hash: str = ""
    bytes: int = 0
    path: str = ""
    source_status: str = "not_connected"


class EvidenceResponse(BaseModel):
    evidence_id: str
    evidence_type: str
    evidence_status: str
    validation_status: str
    stage: str = ""
    claim: str = ""
    summary: str = ""
    gap_description: Optional[str] = None
    blocking: bool = False
    source_status: str = "not_connected"


class TraceResponse(BaseModel):
    trace_id: str
    trace_type: str
    run_id: Optional[str] = None
    project_id: Optional[str] = None
    stage: Optional[str] = None
    action: str = ""
    summary: str = ""
    graph_status: str = "not_connected"
    transition_mode: str = "unknown"
    persistence: str = "volatile"
    created_at: str = ""


class AuditResponse(BaseModel):
    audit_id: str
    audit_type: str
    gate_id: Optional[str] = None
    risk_level: str = "L0"
    action: str = ""
    decision: str = ""
    reason: str = ""
    project_id: Optional[str] = None
    run_id: Optional[str] = None
    stage: Optional[str] = None
    transition_mode: str = "unknown"
    persistence: str = "volatile"
    created_at: str = ""


class EvidenceGapResponse(BaseModel):
    gap_id: str
    evidence_type: str
    stage: str = ""
    description: str = ""
    blocking: bool = False
    source_status: str = "not_connected"
