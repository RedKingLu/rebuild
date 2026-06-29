"""Agent Selector — stage/task→Agent 映射 + bound_skills/bound_tools 绑定解析 (R9-5-3, T-8).

Public API:
  select_agent(stage, task_type) -> dict | None
  resolve_bindings(agent_dict, stage, max_chars_per_skill) -> dict
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("rebuild.agent_selector")

# ── stage/task_type → agent_type 映射 ───────────────────────────────────────
# (D-091 / context_assembler§4.2c)
_STAGE_AGENT_MAP: dict[str, str] = {
    "p0": "node_worker",
    "p1": "node_worker",
    "p2": "node_worker",
    "p3": "node_worker",
    "p4": "node_worker",
    "p5": "node_worker",
    "p6": "node_worker",
}

_TASK_AGENT_MAP: dict[str, str] = {
    "profiling": "node_worker",
    "onboarding": "node_worker",
    "acceptance": "acceptance",
    "review": "auto_review",
    "gate": "conversation_gate",
    "expert": "expert",
    "chat": "conversation_gate",
}


def select_agent(stage: str, task_type: str = "default") -> Optional[dict]:
    """Select the appropriate Agent definition from DB for a given stage/task.

    Returns the agent as a dict (with context_recipe, bound_skills, bound_tools),
    or None if no agent is configured.
    """
    agent_type = _TASK_AGENT_MAP.get(task_type) or _STAGE_AGENT_MAP.get(stage, "node_worker")
    try:
        from app.core.database import get_session
        from app.models.agent_definition import AgentDefinition, AgentType
        db = get_session()
        try:
            at = AgentType(agent_type) if agent_type in [e.value for e in AgentType] else AgentType.node_worker
            agent = db.query(AgentDefinition).filter(AgentDefinition.agent_type == at).first()
            if not agent:
                logger.warning("No agent found for type=%s", agent_type)
                return None
            return {
                "agent_id": agent.agent_id,
                "agent_type": agent.agent_type.value if hasattr(agent.agent_type, "value") else str(agent.agent_type),
                "name": agent.name,
                "responsibilities": agent.responsibilities or "",
                "forbidden": agent.forbidden or "",
                "model_policy_ref": agent.model_policy_ref or "system-default",
                "context_recipe": agent.context_recipe or {},
                "bound_skills": agent.bound_skills or [],
                "bound_tools": agent.bound_tools or [],
            }
        finally:
            db.close()
    except Exception as e:
        logger.warning("select_agent failed for stage=%s task=%s: %s", stage, task_type, e)
        return None


def resolve_bindings(
    agent: dict,
    stage: str,
    max_chars_per_skill: int = 4000,
) -> dict:
    """Resolve bound_skills → Skill dicts with body; bound_tools → tool refs + schema placeholders.

    Returns:
      {
        "skills": [{"skill_id": ..., "name": ..., "body": ..., "capability_status": ...}],
        "tools": [{"tool_id": ..., "schema": None, "capability_status": "schema_pending_r954"}],
      }
    """
    from app.services.skill_loader import load_skill_body

    skill_refs: list = agent.get("bound_skills") or []
    tool_refs: list = agent.get("bound_tools") or []

    # Resolve skills: each ref is a skill_id or dict {skill_id, directory_path}
    resolved_skills: list[dict] = []
    if skill_refs:
        try:
            from app.core.database import get_session
            from app.models.skill_definition import SkillDefinition
            db = get_session()
            try:
                for ref in skill_refs:
                    sid = ref if isinstance(ref, str) else ref.get("skill_id", "")
                    if not sid:
                        continue
                    s = db.get(SkillDefinition, sid)
                    if s and s.directory_path:
                        loaded = load_skill_body(s.directory_path, max_chars_per_skill)
                        loaded["skill_id"] = sid
                        resolved_skills.append(loaded)
                    elif s:
                        resolved_skills.append({
                            "skill_id": sid, "name": s.name,
                            "body": "", "capability_status": "not_connected",
                            "not_connected_reason": "no directory_path",
                        })
            finally:
                db.close()
        except Exception as e:
            logger.warning("resolve_bindings skills failed: %s", e)

    # Stage-default skills when no explicit binding
    if not resolved_skills:
        from app.services.skill_loader import load_skills_for_stage
        try:
            from app.core.database import get_session
            from app.models.skill_definition import SkillDefinition, SkillSeries, SkillStatus, SkillCategory
            db = get_session()
            try:
                skills_db_rows = db.query(SkillDefinition).filter(
                    SkillDefinition.series == SkillSeries.P,
                    SkillDefinition.category.in_([SkillCategory.common, SkillCategory(stage) if stage else SkillCategory.common]),
                    SkillDefinition.status.in_([SkillStatus.active, SkillStatus.platform_runtime, SkillStatus.planned]),
                    SkillDefinition.enabled == True,
                ).all()
                skills_db = [
                    {"skill_id": s.skill_id, "name": s.name, "directory_path": s.directory_path or "",
                     "category": s.category.value if hasattr(s.category, "value") else str(s.category)}
                    for s in skills_db_rows
                ]
            finally:
                db.close()
            resolved_skills = load_skills_for_stage(stage, max_chars_per_skill, skills_db=skills_db)
        except Exception as e:
            logger.warning("resolve_bindings stage-default skills failed: %s", e)
            from app.services.skill_loader import load_skills_for_stage
            resolved_skills = load_skills_for_stage(stage, max_chars_per_skill)

    # Resolve tools: schema placeholder, real binding in R9-5-4
    resolved_tools = [
        {
            "tool_id": ref if isinstance(ref, str) else ref.get("tool_id", ""),
            "schema": None,
            "capability_status": "schema_pending_r954",
        }
        for ref in tool_refs
    ]

    return {"skills": resolved_skills, "tools": resolved_tools}
