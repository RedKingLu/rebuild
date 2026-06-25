"""Project service — CRUD operations on mock projects."""

from typing import Optional

from app.repositories.fixtures import seed_projects
from app.schemas.project import ProjectResponse, ProjectCreate
from app.schemas.common import Meta


class ProjectService:
    def __init__(self, services):
        self._svc = services
        self._projects: dict[str, ProjectResponse] = {}
        self._seed()

    def _seed(self):
        for p in seed_projects():
            self._projects[p.project_id] = p

    def list(self, status: Optional[str] = None) -> list[ProjectResponse]:
        projects = list(self._projects.values())
        if status:
            projects = [p for p in projects if p.project_status == status]
        return projects

    def get(self, project_id: str) -> Optional[ProjectResponse]:
        return self._projects.get(project_id)

    def create(self, req: ProjectCreate) -> ProjectResponse:
        import uuid
        pid = f"proj-{uuid.uuid4().hex[:6]}"
        p = ProjectResponse(
            project_id=pid,
            name=req.name,
            description=req.description,
            project_status="created",
            source_type=req.source_type,
            workspace_status="ready",
            onboarding_done=False,
            created_at=_now(),
            updated_at=_now(),
        )
        self._projects[pid] = p
        return p

    def update(self, project_id: str, **fields) -> Optional[ProjectResponse]:
        p = self._projects.get(project_id)
        if p is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(p, k):
                setattr(p, k, v)
        return p

    def archive(self, project_id: str) -> bool:
        p = self._projects.get(project_id)
        if p is None:
            return False
        p.project_status = "archived"
        return True


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
