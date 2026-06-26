"""Service container for dependency injection.

Uses module-level singleton pattern to ensure all API routes
share the same service instances (and same in-memory writers).
"""

from app.core.config import Settings

_services = None


class Services:
    """Singleton container for all backend services.

    Writers (trace_writer, audit_writer) are created once and reused.
    Other services are created lazily and cached.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._trace_writer = None
        self._audit_writer = None
        self._project_service = None
        self._run_service = None
        self._stage_service = None
        self._gate_service = None
        self._aet_service = None
        self._workspace_service = None
        self._event_service = None
        self._model_gateway = None

    @property
    def model_gateway(self):
        if self._model_gateway is None:
            from app.services.model_gateway import get_model_gateway
            self._model_gateway = get_model_gateway()
        return self._model_gateway

    @property
    def trace_writer(self):
        if self._trace_writer is None:
            from app.core.trace_writer import TraceWriter
            self._trace_writer = TraceWriter()
        return self._trace_writer

    @property
    def audit_writer(self):
        if self._audit_writer is None:
            from app.core.audit_writer import AuditWriter
            self._audit_writer = AuditWriter()
        return self._audit_writer

    @property
    def project_service(self):
        # DEPRECATED: ProjectService is now DB-backed per-request.
        # Routes should use ProjectService(db) directly via Depends(get_session).
        # Kept for backward compatibility with workspace_service, routes_runs,
        # and routes_workspace which call svc.project_service.get(project_id).
        if self._project_service is None:
            from app.services.project_service import ProjectService
            from app.core.database import get_session
            db = get_session()
            self._project_service = ProjectService(db)
        return self._project_service

    @property
    def run_service(self):
        if self._run_service is None:
            from app.services.run_service import RunService
            self._run_service = RunService(self)
        return self._run_service

    @property
    def stage_service(self):
        if self._stage_service is None:
            from app.services.stage_service import StageService
            self._stage_service = StageService(self)
        return self._stage_service

    @property
    def gate_service(self):
        if self._gate_service is None:
            from app.services.gate_service import GateService
            self._gate_service = GateService(self)
        return self._gate_service

    @property
    def aet_service(self):
        if self._aet_service is None:
            from app.services.aet_service import AETService
            self._aet_service = AETService(self)
        return self._aet_service

    @property
    def workspace_service(self):
        if self._workspace_service is None:
            from app.services.workspace_service import WorkspaceService
            self._workspace_service = WorkspaceService(self)
        return self._workspace_service

    @property
    def event_service(self):
        if self._event_service is None:
            from app.services.event_service import EventService
            self._event_service = EventService(self)
        return self._event_service


def get_services(settings: Settings | None = None) -> Services:
    """Return the module-level Services singleton."""
    global _services
    if _services is None:
        if settings is None:
            settings = Settings()
        _services = Services(settings)
    return _services


def clear_services_cache():
    """Clear the singleton for test isolation."""
    global _services
    _services = None
