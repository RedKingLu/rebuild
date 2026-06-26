"""Toggle API — 启用 / 禁用 Skill / Agent / Resource。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import SuccessEnvelope
from app.services.agent_service import AgentService
from app.services.registry_service import RegistryService
from app.services.skill_service import SkillService

toggle_router = APIRouter(prefix="/toggle", tags=["toggle"])


@toggle_router.patch("/skill/{skill_id}")
def toggle_skill(skill_id: str, db: Session = Depends(get_db)):
    svc = SkillService(db)
    skill = svc.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skill.enabled = not skill.enabled
    skill.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(skill)
    return SuccessEnvelope(
        data={"skill_id": skill_id, "enabled": skill.enabled},
        meta={"source_status": "real"},
    )


@toggle_router.patch("/agent/{agent_id}")
def toggle_agent(agent_id: str, db: Session = Depends(get_db)):
    svc = AgentService(db)
    agent = svc.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    from app.models.agent_definition import AgentCategory
    if agent.category == AgentCategory.system:
        raise HTTPException(status_code=403, detail="系统 Agent 不可禁用")
    agent.enabled = not agent.enabled
    agent.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(agent)
    return SuccessEnvelope(
        data={"agent_id": agent_id, "enabled": agent.enabled},
        meta={"source_status": "real"},
    )


@toggle_router.patch("/resource/{resource_id}")
def toggle_resource(resource_id: str, db: Session = Depends(get_db)):
    svc = RegistryService(db)
    entry = svc.get(resource_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Resource not found")
    entry.enabled = not entry.enabled
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return SuccessEnvelope(
        data={"resource_id": resource_id, "enabled": entry.enabled},
        meta={"source_status": "real"},
    )
