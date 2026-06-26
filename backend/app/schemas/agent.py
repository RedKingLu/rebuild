"""Pydantic schemas for Agent Definition (Phase 12 扩展)."""

from datetime import datetime
from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    agent_type: str = Field(default="expert", pattern="^(node_worker|acceptance|auto_review|expert|conversation_gate)$")
    category: str = Field(default="expert", pattern="^(system|expert)$")
    name: str = Field(..., min_length=1, max_length=255)
    version: str = "1.0.0"
    enabled: bool = True
    responsibilities: str = ""
    forbidden: str = ""
    model_policy_ref: str | None = None
    context_recipe: dict | None = None
    memory_policy: dict | None = None
    custom_prompt: str | None = None
    bound_skills: list[str] | None = None
    bound_tools: list[str] | None = None
    bound_mcps: list[str] | None = None
    origin: str = "user_created"  # local / community_import / user_created


class AgentUpdate(BaseModel):
    name: str | None = None
    version: str | None = None
    status: str | None = None
    enabled: bool | None = None
    responsibilities: str | None = None
    forbidden: str | None = None
    model_policy_ref: str | None = None
    context_recipe_ref: str | None = None
    memory_policy_ref: str | None = None
    gate_rules: str | None = None
    failure_escalation: str | None = None
    tool_policy_ref: str | None = None
    skill_policy_ref: str | None = None
    mcp_policy_ref: str | None = None
    input_output_contract: str | None = None
    artifact_evidence_contract: str | None = None
    self_check_acceptance: str | None = None
    context_recipe: dict | None = None
    memory_policy: dict | None = None
    custom_prompt: str | None = None
    bound_skills: list[str] | None = None
    bound_tools: list[str] | None = None
    bound_mcps: list[str] | None = None
    credential_ref: str | None = None


class AgentResponse(BaseModel):
    agent_id: str
    agent_type: str
    category: str = "expert"
    name: str
    version: str
    status: str
    enabled: bool = True

    # Contract 13 elements
    responsibilities: str = ""
    forbidden: str = ""
    context_recipe_ref: str | None = None
    memory_policy_ref: str | None = None
    model_policy_ref: str | None = None
    gate_rules: str = ""
    failure_escalation: str = ""
    tool_policy_ref: str | None = None
    skill_policy_ref: str | None = None
    mcp_policy_ref: str | None = None
    input_output_contract: str = ""
    artifact_evidence_contract: str = ""
    self_check_acceptance: str = ""

    # Context Recipe & Memory Policy
    context_recipe: dict | None = None
    memory_policy: dict | None = None

    # Custom prompt + bound resources
    custom_prompt: str | None = None
    bound_skills: list | None = None
    bound_tools: list | None = None
    bound_mcps: list | None = None

    # ModelGateway reference (no Key)
    credential_ref: str | None = None

    # Origin
    origin: str = "user_created"

    source_status: str = "real"
    capability_status: str = "active"

    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentListData(BaseModel):
    agents: list[AgentResponse]
    total: int
    source_status: str = "real"
    capability_status: str = "active"
