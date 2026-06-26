"""Workspace service — aggregate data for the Workspace frontend view.

R4: Assembles mock data from Project/Run/Stage/Gate/AET services.
All data carries source_status="mock".
No real file system is read.
"""

from typing import Optional

from app.schemas.workspace import WorkspaceAggregateResponse, GraphStatus, FileIndex
from app.schemas.project import ProjectResponse
from app.schemas.common import Meta
from app.services.project_service import ProjectService


class WorkspaceService:
    def __init__(self, services):
        self._svc = services

    def aggregate(self, project_id: str, include: Optional[str] = None) -> WorkspaceAggregateResponse:
        """Assemble full workspace aggregate for a project.

        Args:
            project_id: The project to query
            include: Comma-separated list of sections (run,stages,gate,aet)
        """
        svc = self._svc

        project = svc.project_service.get(project_id)
        active_run = None
        stage_statuses = {}
        active_gate = None
        pending_gates = []
        recent_artifacts = []
        pending_evidence_gaps = []
        recent_traces = []
        recent_audits = []

        if project and project.current_run_id:
            active_run = svc.run_service.get(project.current_run_id)
            if active_run:
                stage_statuses = active_run.stage_status

        active_gate = svc.gate_service.get_active(project_id)
        pending_gates = svc.gate_service.list_by_project(project_id)

        recent_artifacts = svc.aet_service.list_artifacts(project_id=project_id)[:10]
        pending_evidence_gaps = svc.aet_service.list_evidence_gaps()
        recent_traces = svc.aet_service.list_traces(project_id=project_id, limit=20)
        recent_audits = svc.aet_service.list_audits(project_id=project_id, limit=20)

        return WorkspaceAggregateResponse(
            project=ProjectResponse(**ProjectService.to_response(project)) if project else None,
            active_run=active_run,
            stage_statuses=stage_statuses,
            active_gate=active_gate,
            pending_gates=pending_gates,
            recent_artifacts=recent_artifacts,
            pending_evidence_gaps=pending_evidence_gaps,
            recent_traces=recent_traces,
            recent_audits=recent_audits,
            file_index=_mock_file_index(),
            graph_status=GraphStatus(
                graph_capability_status="not_connected",
                transition_mode="mock",
            ),
            meta=Meta(),
        )


def _mock_file_index() -> list[FileIndex]:
    return [
        FileIndex(key="artifacts", label="产出物（只读）", editable=False, children=[
            {"name": "tech-stack-report.md", "path": "artifacts/tech-stack-report.md", "type": "file", "editable": False},
            {"name": "risk-matrix.json", "path": "artifacts/risk-matrix.json", "type": "file", "editable": False},
        ]),
        FileIndex(key="source", label="源代码（只读）", editable=False, children=[
            {"name": "src/", "path": "source/src", "type": "dir", "editable": False},
        ]),
    ]
