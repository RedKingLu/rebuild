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
    """Startup: init DB, seed data, warm services. Shutdown: clear cache."""
    settings = Settings()
    # Initialize database and seed
    from app.core.database import get_session, init_db
    init_db()
    db = get_session()
    try:
        from app.seed import seed_all
        counts = seed_all(db)
        if any(v > 0 for v in counts.values()):
            import logging
            logging.getLogger("uvicorn").info(f"R6 seed data: {counts}")
    finally:
        db.close()
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
from app.api.routes_models import model_router, assistant_router
app.include_router(model_router, prefix="/api")
app.include_router(assistant_router, prefix="/api")

# R7: Real Integration endpoints (replaces placeholder in routes_models)
from app.api.routes_integrations import integration_router
app.include_router(integration_router, prefix="/api")

# R7: Dashboard stats (for overview page real data)
from app.api.routes_dashboard import router as dashboard_router
app.include_router(dashboard_router, prefix="/api")

# W12: SSE / Events
from app.api.routes_events import router as events_router
app.include_router(events_router, prefix="/api")

# R6: Agent / Skill / Resource Registry / Credential (BYOK)
from app.api.routes_agents import agent_router
app.include_router(agent_router, prefix="/api")

from app.api.routes_skills import skill_router
app.include_router(skill_router, prefix="/api")

from app.api.routes_registry import registry_router
app.include_router(registry_router, prefix="/api")

from app.api.routes_credentials import credential_router
app.include_router(credential_router, prefix="/api")

# R12: Import endpoints (Agent/Skill/Resource local + community)
from app.api.routes_imports import import_router
app.include_router(import_router, prefix="/api")

# R12: MCP server management (real MCP protocol)
from app.api.routes_mcp import mcp_router
app.include_router(mcp_router, prefix="/api")

# R7 new: Upload (zip 上传创建 + source 扫描入库)
from app.api.routes_upload import upload_router
app.include_router(upload_router, prefix="/api")

# R7 new: Export (zip 下载)
from app.api.routes_export import export_router
app.include_router(export_router, prefix="/api")

# R7 new: Toggle (启用/禁用)
from app.api.routes_toggle import toggle_router
app.include_router(toggle_router, prefix="/api")
