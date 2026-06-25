"""Resource domain schemas — placeholder for R6 Agent/Skill/Resource."""

from pydantic import BaseModel, Field


class ResourceResponse(BaseModel):
    resource_id: str = ""
    resource_type: str = ""
    name: str = ""
    description: str = ""
    source_status: str = "not_connected"
    capability_status: str = "future"


class ResourceRegistryResponse(BaseModel):
    resources: list[ResourceResponse] = Field(default_factory=list)
    total: int = 0
    source_status: str = "not_connected"
