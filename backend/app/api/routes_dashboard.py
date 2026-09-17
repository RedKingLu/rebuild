"""Dashboard stats API — aggregate overview page statistics from real data sources."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import SuccessEnvelope, Meta
from app.services.project_service import ProjectService
from app.services.integration_service import IntegrationSummaryService
from app.models.project import ProjectStatus

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats(db: Session = Depends(get_db)):
    """Aggregate statistics for the overview page.

    Only includes metrics backed by real data sources.
    Gate/Risk metrics are excluded (not yet real-ified, R9+).
    """
    project_svc = ProjectService(db)
    integration_svc = IntegrationSummaryService(db)

    projects = project_svc.list()
    projects_total = len(projects)
    running_count = sum(1 for p in projects
                        if hasattr(p, 'project_status') and
                        (p.project_status == ProjectStatus.running if hasattr(p.project_status, 'value')
                         else p.project_status == 'running'))

    integration_summary = integration_svc.get_summary()

    return SuccessEnvelope(data={
        "projects": {
            "running": running_count,
            "total": projects_total,
        },
        "integrations": integration_summary,
        "source_status": "real",
        "note": "Gate/Risk metrics not included — domain not yet real-ified (R9+)",
    }, meta=Meta(source_status="real", capability_status="available"))
