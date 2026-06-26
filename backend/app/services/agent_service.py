"""Agent Definition service — CRUD for Agent contracts (Phase 12 扩展)."""

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.agent_definition import AgentDefinition, AgentType, AgentCategory, DefinitionStatus
from app.schemas.agent import AgentCreate, AgentUpdate


class AgentService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: AgentCreate) -> AgentDefinition:
        agent = AgentDefinition(
            agent_type=AgentType(data.agent_type),
            category=AgentCategory(data.category),
            name=data.name,
            version=data.version,
            enabled=data.enabled,
            status=DefinitionStatus.draft if data.category == "expert" else DefinitionStatus.active,
            responsibilities=data.responsibilities,
            forbidden=data.forbidden,
            model_policy_ref=data.model_policy_ref,
            context_recipe=data.context_recipe,
            memory_policy=data.memory_policy,
            custom_prompt=data.custom_prompt,
            bound_skills=data.bound_skills,
            bound_tools=data.bound_tools,
            bound_mcps=data.bound_mcps,
            origin=data.origin,
        )
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self.db.get(AgentDefinition, agent_id)

    def list_all(self, agent_type: str | None = None, category: str | None = None,
                 limit: int = 100, offset: int = 0) -> tuple[list[AgentDefinition], int]:
        q = self.db.query(AgentDefinition)
        if agent_type:
            q = q.filter(AgentDefinition.agent_type == AgentType(agent_type))
        if category:
            q = q.filter(AgentDefinition.category == AgentCategory(category))
        total = q.count()
        records = q.order_by(AgentDefinition.category, AgentDefinition.name).offset(offset).limit(limit).all()
        return records, total

    def update(self, agent_id: str, data: AgentUpdate) -> AgentDefinition | None:
        agent = self.get(agent_id)
        if agent is None:
            return None
        # 系统 Agent 不可修改 prompt 和绑定的 skill/tool/MCP
        is_system = (agent.category == AgentCategory.system)
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if is_system and key in ("custom_prompt", "bound_skills", "bound_tools", "bound_mcps",
                                      "skill_policy_ref", "tool_policy_ref", "mcp_policy_ref"):
                continue  # 系统 Agent 不可修改这些字段
            if key == "agent_type" and value:
                setattr(agent, key, AgentType(value))
            elif key == "status" and value:
                setattr(agent, key, DefinitionStatus(value))
            elif hasattr(agent, key):
                setattr(agent, key, value)
        agent.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def delete(self, agent_id: str) -> bool:
        agent = self.get(agent_id)
        if agent is None:
            return False
        # 系统 Agent 不可删除
        if agent.category == AgentCategory.system:
            return False
        self.db.delete(agent)
        self.db.commit()
        return True

    @staticmethod
    def to_response(agent: AgentDefinition) -> dict:
        return {
            "agent_id": agent.agent_id,
            "agent_type": agent.agent_type.value if agent.agent_type else "expert",
            "category": agent.category.value if agent.category else "expert",
            "name": agent.name,
            "version": agent.version,
            "status": agent.status.value if agent.status else "draft",
            "enabled": agent.enabled,
            "responsibilities": agent.responsibilities or "",
            "forbidden": agent.forbidden or "",
            "context_recipe_ref": agent.context_recipe_ref,
            "memory_policy_ref": agent.memory_policy_ref,
            "model_policy_ref": agent.model_policy_ref,
            "gate_rules": agent.gate_rules or "",
            "failure_escalation": agent.failure_escalation or "",
            "tool_policy_ref": agent.tool_policy_ref,
            "skill_policy_ref": agent.skill_policy_ref,
            "mcp_policy_ref": agent.mcp_policy_ref,
            "input_output_contract": agent.input_output_contract or "",
            "artifact_evidence_contract": agent.artifact_evidence_contract or "",
            "self_check_acceptance": agent.self_check_acceptance or "",
            "context_recipe": agent.context_recipe,
            "memory_policy": agent.memory_policy,
            "custom_prompt": agent.custom_prompt,
            "bound_skills": agent.bound_skills,
            "bound_tools": agent.bound_tools,
            "bound_mcps": agent.bound_mcps,
            "credential_ref": agent.credential_ref,
            "origin": agent.origin,
            "source_status": agent.source_status,
            "capability_status": agent.capability_status,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }
