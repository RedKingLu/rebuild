"""Project domain schemas."""

from typing import Optional, Literal
from pydantic import BaseModel, Field
from app.schemas.common import Meta, GraphPlaceholderFields


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    source_type: Literal["local_dir", "git", "zip", "github", "manual"] = "local_dir"
    source_config: Optional[dict] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ProjectSourceUpdate(BaseModel):
    """Schema for updating project source type and configuration."""
    source_type: Literal["local_dir", "git", "zip", "github", "manual"]
    source_config: Optional[dict] = None


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    description: str
    project_status: str
    current_stage: Optional[str] = None
    current_run_id: Optional[str] = None
    active_gate: Optional[str] = None
    evidence_gap_count: int = 0
    source_type: str = "local_dir"
    source_config: Optional[dict] = None
    workspace_status: str = "ready"
    onboarding_done: bool = False
    updated_at: str = ""
    created_at: str = ""

    # Capability marking
    source_status: str = "mock"
    capability_status: str = "not_connected"


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse] = Field(default_factory=list)
    total: int = 0
    meta: Meta = Field(default_factory=Meta)
