"""rebuild platform backend — FastAPI application entry point.

R4: Basic engineering skeleton with mock/in-memory API.
No real LangGraph graph, model calls, agent execution, or Git operations.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings
from app.dependencies import get_services, clear_services_cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: warm services. Shutdown: clear cache."""
    settings = Settings()
    get_services(settings)
    yield
    clear_services_cache()


app = FastAPI(
    title="rebuild 平台后端",
    version="V26.1.1",
    lifespan=lifespan,
)

# CORS — allow frontend dev server
_settings = Settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all routers (W2-W12)
# W2: health/version/meta
from app.api.routes_health import router as health_router
app.include_router(health_router, prefix="/api")

# W7: Project / Run / Stage
from app.api.routes_projects import router as projects_router
app.include_router(projects_router, prefix="/api")

from app.api.routes_runs import router as runs_router
app.include_router(runs_router, prefix="/api")

from app.api.routes_stages import router as stages_router
app.include_router(stages_router, prefix="/api")

# W8: Gate / Authorization
from app.api.routes_gates import router as gates_router
app.include_router(gates_router, prefix="/api")

# W9: AET
from app.api.routes_aet import router as aet_router
app.include_router(aet_router, prefix="/api")

# W10: Workspace
from app.api.routes_workspace import router as workspace_router
app.include_router(workspace_router, prefix="/api")

# W11: Model / Resource / Integration (R5: ModelGateway full implementation)
from app.api.routes_models import model_router, resource_router, integration_router, assistant_router
app.include_router(model_router, prefix="/api")
app.include_router(resource_router, prefix="/api")
app.include_router(integration_router, prefix="/api")
app.include_router(assistant_router, prefix="/api")

# W12: SSE / Events
from app.api.routes_events import router as events_router
app.include_router(events_router, prefix="/api")
