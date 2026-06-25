"""Integration / Source / Git domain schemas — placeholder for R7."""

from typing import Optional, Literal
from pydantic import BaseModel, Field


class IntegrationResponse(BaseModel):
    integration_id: str = ""
    integration_type: str = ""
    name: str = ""
    status: str = "not_connected"
    source_status: str = "not_connected"
    capability_status: str = "future"


class GitStatusResponse(BaseModel):
    repository_status: str = "not_connected"
    branch: str = ""
    last_commit: Optional[str] = None
    source_status: str = "not_connected"
    capability_status: str = "future"


class SourceImportRequest(BaseModel):
    source_type: Literal["local_dir", "git", "zip", "github"] = "local_dir"
    source_path: str = ""
    source_uri: Optional[str] = None


class SourceImportResponse(BaseModel):
    import_job_id: str = ""
    status: str = "not_connected"
    source_status: str = "not_connected"
    capability_status: str = "future"
