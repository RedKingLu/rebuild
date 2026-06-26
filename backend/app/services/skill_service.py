"""Skill Definition service — CRUD with R/P series isolation."""

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.skill_definition import SkillDefinition, SkillSeries, SkillCategory, SkillStatus
from app.schemas.skill import SkillCreate, SkillUpdate


class SkillService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: SkillCreate) -> SkillDefinition:
        skill = SkillDefinition(
            name=data.name,
            series=SkillSeries(data.series),
            category=SkillCategory(data.category),
            description=data.description,
            required_model_policy=data.required_model_policy,
            required_tools=data.required_tools,
            required_context=data.required_context,
            status=SkillStatus(data.status) if data.status else SkillStatus.draft,
            skill_source=data.skill_source,
            directory_path=data.directory_path,
            enabled=data.enabled if data.enabled is not None else True,
        )
        self.db.add(skill)
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self.db.get(SkillDefinition, skill_id)

    def list_all(self, series: str | None = None, category: str | None = None,
                 limit: int = 100, offset: int = 0) -> tuple[list[SkillDefinition], int]:
        q = self.db.query(SkillDefinition)
        if series:
            try:
                q = q.filter(SkillDefinition.series == SkillSeries(series))
            except ValueError:
                return [], 0
        if category:
            try:
                q = q.filter(SkillDefinition.category == SkillCategory(category))
            except ValueError:
                return [], 0
        total = q.count()
        records = q.order_by(SkillDefinition.series, SkillDefinition.category, SkillDefinition.name).offset(offset).limit(limit).all()
        return records, total

    def update(self, skill_id: str, data: SkillUpdate) -> SkillDefinition | None:
        skill = self.get(skill_id)
        if skill is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key == "series" and value:
                setattr(skill, key, SkillSeries(value))
            elif key == "category" and value:
                setattr(skill, key, SkillCategory(value))
            elif key == "status" and value:
                setattr(skill, key, SkillStatus(value))
            elif hasattr(skill, key):
                setattr(skill, key, value)
        skill.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def delete(self, skill_id: str) -> bool:
        skill = self.get(skill_id)
        if skill is None:
            return False
        self.db.delete(skill)
        self.db.commit()
        return True

    @staticmethod
    def to_response(skill: SkillDefinition) -> dict:
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "version": skill.version,
            "series": skill.series.value,
            "category": skill.category.value,
            "description": skill.description,
            "required_model_policy": skill.required_model_policy,
            "required_tools": skill.required_tools,
            "required_context": skill.required_context,
            "status": skill.status.value,
            "skill_source": skill.skill_source,
            "directory_path": skill.directory_path,
            "enabled": skill.enabled,
            "source_status": skill.source_status,
            "capability_status": skill.capability_status,
            "created_at": skill.created_at,
            "updated_at": skill.updated_at,
        }
