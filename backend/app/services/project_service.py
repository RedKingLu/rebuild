"""Project service — DB-backed CRUD operations on projects."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectStatus, SourceType
from app.schemas.project import ProjectCreate


class ProjectService:
    def __init__(self, db: Session):
        self.db = db

    def list(self, status: Optional[str] = None, sort: str = "updated_at",
             order: str = "desc", limit: Optional[int] = None,
             offset: int = 0, include_archived: bool = False) -> list[Project]:
        q = self.db.query(Project)
        if status:
            # Explicit status filter takes precedence (e.g. status=archived must be
            # listable); do NOT also apply the default "exclude archived" filter,
            # which would make status=archived logically impossible (always empty).
            q = q.filter(Project.project_status == status)
        elif not include_archived:
            q = q.filter(Project.project_status != ProjectStatus.archived)
        # sort
        col = getattr(Project, sort, Project.updated_at)
        if order == "asc":
            q = q.order_by(col.asc())
        else:
            q = q.order_by(col.desc())
        if limit is not None:
            q = q.limit(limit)
        q = q.offset(offset)
        return q.all()

    def get(self, project_id: str) -> Optional[Project]:
        return self.db.get(Project, project_id)

    def create(self, req: ProjectCreate) -> Project:
        source_config = None
        if req.source_type in ("git", "github") and req.source_config:
            source_config = req.source_config
        elif req.source_type in ("local_dir", "zip") and req.source_config:
            source_config = req.source_config
        elif req.source_type == "manual" and req.source_config:
            source_config = req.source_config

        # Map "zip" to enum member zip_source (value is "zip")
        st = req.source_type
        if st == "zip":
            st = "zip"  # SourceType("zip") looks up by value "zip" -> zip_source

        p = Project(
            name=req.name,
            description=req.description or "",
            source_type=SourceType(st),
            source_config=source_config,
            project_status=ProjectStatus.created,
        )
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        return p

    def update(self, project_id: str, **fields) -> Optional[Project]:
        p = self.get(project_id)
        if p is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(p, k):
                setattr(p, k, v)
        p.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(p)
        return p

    def update_source(self, project_id: str, source_type: str,
                      source_config: Optional[dict] = None) -> Optional[Project]:
        """Update project source configuration (used by Git/ZIP/GitHub integration endpoints)."""
        p = self.get(project_id)
        if p is None:
            return None
        # Map "zip" to value "zip" for enum lookup
        st = source_type
        if st == "zip":
            st = "zip"
        p.source_type = SourceType(st)
        if source_config is not None:
            p.source_config = source_config
        p.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(p)
        return p

    def delete(self, project_id: str) -> bool:
        """Soft-delete: set status to archived."""
        p = self.get(project_id)
        if p is None:
            return False
        p.project_status = ProjectStatus.archived
        p.archived_at = datetime.now(timezone.utc)
        p.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    @staticmethod
    def to_response(p: Project) -> dict:
        return {
            "project_id": p.project_id,
            "name": p.name,
            "description": p.description or "",
            "project_status": p.project_status.value if hasattr(p.project_status, 'value') else str(p.project_status),
            "source_type": p.source_type.value if hasattr(p.source_type, 'value') else str(p.source_type),
            "source_config": p.source_config,
            "current_stage": p.current_stage,
            "current_run_id": p.current_run_id,
            "active_gate": p.active_gate,
            "evidence_gap_count": p.evidence_gap_count,
            "workspace_status": p.workspace_status or "ready",
            "onboarding_done": p.onboarding_done,
            "coding_agent_ref": p.coding_agent_ref,
            # D-088 / R9-5-5 — must be surfaced so the B-6 PATCH response round-trips the new scope
            # and the Workspace toolbar switch reflects exactly what should_delegate() will read.
            "external_platform_scope": getattr(p, "external_platform_scope", None) or "none",
            "model_strategy_mode": getattr(p, "model_strategy_mode", "global_unified") or "global_unified",
            "global_model_ref": getattr(p, "global_model_ref", None),
            "created_at": p.created_at.isoformat() if p.created_at else "",
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
            "source_status": "real",
            "capability_status": "available",
        }
