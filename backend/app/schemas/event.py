"""SSE/Event domain schemas."""

from typing import Optional
from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    event_id: str = ""
    event_type: str = ""
    event_version: str = "1.0"
    project_id: Optional[str] = None
    run_id: Optional[str] = None
    stage: Optional[str] = None
    task_graph_id: Optional[str] = None
    node_id: Optional[str] = None
    source: str = "unknown"
    severity: str = "info"
    sequence: int = 0
    cursor: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    trace_ref: Optional[str] = None
    audit_ref: Optional[str] = None
    graph_event_type: Optional[str] = None
    source_status: str = "not_connected"
    created_at: str = ""
