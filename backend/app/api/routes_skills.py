"""Skill Definition API routes — R/P series isolation."""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.schemas.skill import SkillCreate, SkillUpdate, SkillResponse, SkillListData
from app.services.skill_service import SkillService
from app.schemas.common import SuccessEnvelope

skill_router = APIRouter(prefix="/skills", tags=["skills"])


def get_service(db: Session = Depends(get_session)) -> SkillService:
    return SkillService(db)


@skill_router.get("", response_model=SkillListData)
def list_skills(
    series: str | None = None,
    category: str | None = None,
    limit: int = Query(default=100, le=200),
    offset: int = Query(default=0, ge=0),
    svc: SkillService = Depends(get_service),
):
    skills, total = svc.list_all(series=series, category=category, limit=limit, offset=offset)
    return SkillListData(
        skills=[SkillResponse(**svc.to_response(s)) for s in skills],
        total=total,
    )


@skill_router.get("/{skill_id}", response_model=SkillResponse)
def get_skill(skill_id: str, svc: SkillService = Depends(get_service)):
    skill = svc.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillResponse(**svc.to_response(skill))


@skill_router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
def create_skill(data: SkillCreate, svc: SkillService = Depends(get_service)):
    skill = svc.create(data)
    return SkillResponse(**svc.to_response(skill))


@skill_router.put("/{skill_id}", response_model=SkillResponse)
def update_skill(skill_id: str, data: SkillUpdate, svc: SkillService = Depends(get_service)):
    skill = svc.update(skill_id, data)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillResponse(**svc.to_response(skill))


@skill_router.delete("/{skill_id}", response_model=SuccessEnvelope)
def delete_skill(skill_id: str, svc: SkillService = Depends(get_service)):
    if not svc.delete(skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    return SuccessEnvelope(success=True, detail="Skill deleted")
