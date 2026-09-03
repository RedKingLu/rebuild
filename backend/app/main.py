"""rebuild platform backend — FastAPI application entry point.

R4: Basic engineering skeleton with mock/in-memory API.
No real LangGraph graph, model calls, agent execution, or Git operations.
"""

# ── Load .env into os.environ BEFORE any module reads from os.environ ──
#    pydantic-settings reads .env into its Settings object but does NOT
#    inject values into os.environ.  Modules like byok_crypto and the
#    startup REBUILD_MASTER_KEY check read os.environ directly, so we
#    pre-load the .env file here (skip keys already set in the shell).
import os as _load_os


def _preload_env(path: str) -> None:
    """把 path 中的 KEY=VALUE 注入 os.environ；已在 shell 中设置的键不覆盖。

    路径不存在 / 文件为空 / 只有注释时静默跳过。用函数作用域隔离循环变量，
    因此不再需要在守卫块外 `del` 循环变量 —— 原实现无条件
    `del _f, _line, _key, _val`，而这些名字只在守卫块内绑定，导致无 .env、
    空 .env 或全注释 .env 时抛 NameError，后端无法导入启动。
    自行 import os，不依赖模块级别名，避免下方 del 之后失效。
    """
    import os

    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val


_preload_env(_load_os.path.join(_load_os.path.dirname(__file__), "..", ".env"))
del _load_os

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
    from app.core.database import get_session, init_db, verify_migration_head_on_startup
    init_db()
    # R17.2 WP-A: honest startup self-check — is the (SQLite) real DB at the Alembic
    # migration head? create_all only builds missing tables, never adds columns to
    # existing ones, so a persistent DB behind a migration silently lacks columns until
    # a query crashes (R16: resource_entry.package_url). This surfaces drift LOUDLY at
    # ERROR (公理3) without auto-migrating or masking with create_all. Non-fatal.
    verify_migration_head_on_startup()
    db = get_session()
    try:
        from app.seed import seed_all, seed_model_catalog
        counts = seed_all(db)
        catalog_n = seed_model_catalog(db)
        eval_n = seed_exemplar_evaluations(db)
        if any(v > 0 for v in counts.values()) or catalog_n > 0 or eval_n > 0:
            import logging
            logging.getLogger("uvicorn").info(
                f"R6 seed data: {counts}, model_catalog: {catalog_n}, eval: {eval_n}")
    finally:
        db.close()
    get_services(settings)
    # T7/公理7: warn if REBUILD_MASTER_KEY is missing — BYOK encrypt/decrypt will fail
    import os as _os
    if not _os.environ.get("REBUILD_MASTER_KEY"):
        import logging
        logging.getLogger("uvicorn").critical(
            "REBUILD_MASTER_KEY is not set — BYOK credential encryption is disabled. "
            "Add REBUILD_MASTER_KEY=<random-32-byte-hex> to backend/.env. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    # R9-5-1: register real P0/P1 stage handlers + Gate backend onto the LangGraph graph
    try:
        from app.graph.stage_handlers import bootstrap_graph_handlers
        bootstrap_graph_handlers()
    except Exception:
        import logging
        logging.getLogger("uvicorn").warning("graph handler bootstrap failed", exc_info=True)
    # R17.3-6 WP-8 (GAP-BG-1): restart recovery — scan DB for orphaned "running" runs and
    # auto-resume them from their LangGraph checkpoint (process crash / restart resilience).
    # Non-blocking: classification reads run here; heavy resume is offloaded to background
    # threads. Non-fatal — a recovery failure must never block startup (公理3: 发声 not 崩溃).
    try:
        from app.graph.recovery import recover_interrupted_runs
        _rec = await recover_interrupted_runs()
        if _rec.get("scanned"):
            import logging
            logging.getLogger("uvicorn").info(f"R17.3-6 WP-8 run recovery: {_rec}")
    except Exception:
        import logging
        logging.getLogger("uvicorn").warning("run recovery scan failed", exc_info=True)
    yield
    clear_services_cache()
    try:
        from app.graph.checkpoint import close_checkpointer
        await close_checkpointer()
    except Exception:
        import logging
        logging.getLogger("uvicorn").debug("close_checkpointer failed (non-fatal, may be uninitialized)", exc_info=True)


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

# R12-3-C1: P5 输入事实源路由（DB refs + 工作区路径，D-105②）
from app.api.routes_p5_input import router as p5_input_router
app.include_router(p5_input_router, prefix="/api")
# R12-3-C10: P6 交付包路由（package + download）
from app.api.routes_p6_delivery import router as p6_delivery_router
app.include_router(p6_delivery_router, prefix="/api")

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

# R13-4: Fusion configuration API (Fusion as a virtual model, 方案 E)
from app.api.routes_fusion import fusion_router
app.include_router(fusion_router, prefix="/api")

# R6: Agent / Skill / Resource Registry / Credential (BYOK)
from app.api.routes_agents import agent_router
app.include_router(agent_router, prefix="/api")

# R9-3G-C: Agent chat with SSE streaming
from app.api.routes_agent_chat import agent_chat_router
app.include_router(agent_chat_router, prefix="/api")

from app.api.routes_skills import skill_router
app.include_router(skill_router, prefix="/api")

from app.api.routes_registry import registry_router
app.include_router(registry_router, prefix="/api")

from app.api.routes_credentials import credential_router
app.include_router(credential_router, prefix="/api")

# R8-5: Coding Agent configuration (D-077 / D-078)
from app.api.routes_coding_agents import router as coding_agents_router
app.include_router(coding_agents_router, prefix="/api")

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

# R9-5-1: graph driver endpoints (drive the real P0-P6 LangGraph over HTTP)
from app.api.routes_graph import router as graph_router
app.include_router(graph_router, prefix="/api")

# R15-4: Community Connector — proxy + import from the independent community service
from app.api.routes_community import community_router
app.include_router(community_router)  # prefix already /api/community

# R15-4-C7: Knowledge package zip importer + knowledge content
from app.api.routes_knowledge import knowledge_router
app.include_router(knowledge_router, prefix="/api")

# R15-4-C8: ModelCatalog persistent catalog
from app.api.routes_model_catalog import catalog_router
app.include_router(catalog_router, prefix="/api")

# R15-4-C10: AgentModelEvalResult (imported eval results, display-only)
from app.api.routes_model_eval import eval_router, seed_exemplar_evaluations
app.include_router(eval_router, prefix="/api")

# R15-4-C11: OfficialSource empty seam (returns not_connected; no real API)
from app.api.routes_official_sources import official_router
app.include_router(official_router, prefix="/api")
