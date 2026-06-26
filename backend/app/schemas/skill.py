"""Pydantic schemas for Skill Definition."""

from datetime import datetime
from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    series: str = Field(..., pattern="^(R|P)$")
    category: str = Field(...)
    description: str = ""
    required_model_policy: str | None = None
    required_tools: list | None = None
    required_context: list | None = None
    status: str = "draft"
    skill_source: str | None = None
    directory_path: str | None = None
    enabled: bool = True


class SkillUpdate(BaseModel):
    name: str | None = None
    version: str | None = None
    category: str | None = None
    description: str | None = None
    required_model_policy: str | None = None
    required_tools: list | None = None
    required_context: list | None = None
    status: str | None = None
    skill_source: str | None = None
    directory_path: str | None = None
    enabled: bool | None = None


class SkillResponse(BaseModel):
    skill_id: str
    name: str
    version: str
    series: str
    category: str
    description: str = ""
    required_model_policy: str | None = None
    required_tools: list | None = None
    required_context: list | None = None
    status: str
    skill_source: str | None = None
    directory_path: str | None = None
    enabled: bool = True
    source_status: str = "real"
    capability_status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class SkillListData(BaseModel):
    skills: list[SkillResponse]
    total: int
    source_status: str = "real"
    capability_status: str = "active"
