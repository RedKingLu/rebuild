"""Workspace aggregation schema."""

from typing import Optional
from pydantic import BaseModel, Field
from app.schemas.common import Meta
from app.schemas.project import ProjectResponse
from app.schemas.run import RunResponse
from app.schemas.aet import ArtifactResponse, EvidenceResponse, TraceResponse, AuditResponse, EvidenceGapResponse
from app.schemas.gate import GateResponse


class GraphStatus(BaseModel):
    graph_capability_status: str = "not_connected"
    transition_mode: str = "mock"
    checkpoint_ref: Optional[str] = None


class FileIndex(BaseModel):
    key: str = ""
    label: str = ""
    editable: bool = False
    children: list[dict] = Field(default_factory=list)


class WorkspaceAggregateResponse(BaseModel):
    project: Optional[ProjectResponse] = None
    active_run: Optional[RunResponse] = None
    stage_statuses: dict[str, str] = Field(default_factory=dict)
    active_gate: Optional[GateResponse] = None
    pending_gates: list[GateResponse] = Field(default_factory=list)
    recent_artifacts: list[ArtifactResponse] = Field(default_factory=list)
    pending_evidence_gaps: list[EvidenceGapResponse] = Field(default_factory=list)
    recent_traces: list[TraceResponse] = Field(default_factory=list)
    recent_audits: list[AuditResponse] = Field(default_factory=list)
    file_index: list[FileIndex] = Field(default_factory=list)
    graph_status: GraphStatus = Field(default_factory=GraphStatus)
    meta: Meta = Field(default_factory=Meta)
