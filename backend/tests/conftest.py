"""pytest fixtures — isolated data directory + DB per test (absorbed from V26.0 pattern)."""

import pytest
import tempfile
import os
import asyncio

from app.core.config import Settings
from app.dependencies import clear_services_cache, get_services
from app.core.database import _engine, _SessionLocal


@pytest.fixture(autouse=True)
def _maybe_mock_llm(request, monkeypatch):
    """When R176_MOCK_LLM=1 is set, patch litellm.acompletion to return instantly.

    R17-6 makes graph resume run in the background; tests that assert on post-graph
    state need the graph to complete quickly. Real LLM calls can take 14s+ each due to
    provider timeouts. This fixture makes them instant so tests verify graph LOGIC
    (node routing, gate creation, stage advancement) without waiting on providers.

    Usage: R176_MOCK_LLM=1 pytest ...
    """
    if os.environ.get("R176_MOCK_LLM") != "1":
        yield
        return

    class _MockChoice:
        def __init__(self, content="mocked llm response"):
            self.message = type("Msg", (), {"content": content, "tool_calls": None})()

    class _MockResponse:
        def __init__(self):
            self.choices = [_MockChoice()]
            self.usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()

    # 批2: services now route through call_stream (tool loop). When stream=True,
    # litellm.acompletion must return an async iterator of chunks — mirror that so the
    # streaming adapter yields a token then a done sentinel (no tool calls → single round).
    class _MockStreamChoice:
        def __init__(self, content):
            self.delta = type("D", (), {"content": content, "tool_calls": None})()

    class _MockStreamChunk:
        def __init__(self, content="", usage=None):
            self.choices = [_MockStreamChoice(content)] if content is not None else []
            self.usage = usage

    async def _fake_stream(content="mocked llm response"):
        yield _MockStreamChunk(content=content)
        yield _MockStreamChunk(content=None,
                               usage=type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                                    "total_tokens": 2})())

    async def _fake_acompletion(*args, **kwargs):
        if kwargs.get("stream"):
            return _fake_stream()
        await asyncio.sleep(0)  # yield once
        return _MockResponse()

    async def _fake_completion(*args, **kwargs):
        await asyncio.sleep(0)
        return _MockResponse()

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
    monkeypatch.setattr("litellm.completion", _fake_completion)
    yield


@pytest.fixture(autouse=True)
def isolated_data():
    """Isolate test data and database to a temporary directory per test.

    Pattern absorbed from V26.0 tests/conftest.py:
    tmp_path + monkeypatch + cache_clear for test isolation.
    """
    import shutil
    # Reset database globals so get_session picks up the new URL
    import app.core.database as db_mod
    db_mod._engine = None
    db_mod._SessionLocal = None

    tmp = tempfile.mkdtemp(prefix="rebuild-test-")
    db_url = f"sqlite:///{tmp}/rebuild.db"
    ws_tmp = os.path.join(tmp, "workspace")
    settings = Settings(data_dir=tmp, debug=True, database_url=db_url, workspace_dir=ws_tmp)
    # workspace_service / trace_writer / audit_writer AND database.get_engine()
    # all read the GLOBAL settings singleton directly (not the injected one), so
    # every isolated field must be redirected on the global too — otherwise tests
    # pollute (and, for database_url, DROP) the real .data/rebuild.db + 工作区/ tree.
    # B-DB-ISOLATION-1 (R11-3): database_url was NOT overridden here, so
    # get_engine() kept pointing at the real DB and drop_all/create_all below
    # wiped the user's real projects on every pytest run. Redirect it too, and do
    # it BEFORE get_services() so no service can construct a real-DB engine that
    # then gets cached in db_mod._engine and drops the real DB at drop_all.
    # object.__setattr__ bypasses the frozen Settings.
    import app.core.config as cfg
    _orig_ws = cfg.settings.workspace_dir
    _orig_db = cfg.settings.database_url
    _orig_data = cfg.settings.data_dir
    object.__setattr__(cfg.settings, "workspace_dir", ws_tmp)
    object.__setattr__(cfg.settings, "database_url", db_url)
    object.__setattr__(cfg.settings, "data_dir", tmp)
    clear_services_cache()
    svc = get_services(settings)
    # R9-3A: Drop + recreate tables to pick up new columns (SQLite create_all
    # won't alter existing tables, so old project table lacks coding_agent_ref).
    import app.models  # noqa: F401
    from app.models.base import Base
    from app.core.database import get_engine
    # B-DB-ISOLATION-1 safety guard: refuse to drop_all unless the engine is truly
    # bound to this test's temp DB. If a future change breaks the redirect above,
    # this raises loudly instead of silently wiping the real .data/rebuild.db.
    _eng = get_engine()
    if str(_eng.url) != db_url:
        raise RuntimeError(
            f"Test DB isolation broken: engine bound to {_eng.url!r}, expected {db_url!r}. "
            f"Refusing to drop_all on a non-temp database (would delete real data)."
        )
    Base.metadata.drop_all(bind=_eng)
    Base.metadata.create_all(bind=_eng)
    # R9-3E: Seed minimal test data so resource/agent/skill list tests pass
    _seed_test_data()
    # WP-6: bootstrap graph handlers so graph-based execute_onboarding works in tests
    try:
        from app.graph.stage_handlers import bootstrap_graph_handlers, _bootstrapped
        import app.graph.stage_handlers as _sh
        _sh._bootstrapped = False  # force re-bootstrap with test services
        bootstrap_graph_handlers(force=True)
    except Exception:
        pass  # graph bootstrap is best-effort in tests
    # Also reset the FlowRuntime singleton so it re-initializes with test DB
    try:
        from app.graph.runtime import reset_flow_runtime_for_test
        reset_flow_runtime_for_test()
    except Exception:
        pass
    # Reset checkpointer singleton so it re-opens with the test-isolated data_dir
    try:
        import asyncio as _aio
        from app.graph.checkpoint import close_checkpointer
        _loop = _aio.new_event_loop()
        _loop.run_until_complete(close_checkpointer())
        _loop.close()
    except Exception:
        pass
    yield svc
    # V-R17-1B-7: drain any outstanding background graph futures (R17-6 fire-and-forget
    # daemon threads from promotion-decision / gate decision) BEFORE restoring the global
    # settings.database_url. Otherwise a still-running daemon thread may lazily call
    # get_engine()/get_services() AFTER the redirect below is removed and cache a
    # real-DB engine in db_mod._engine, tripping a later test's isolation guard.
    # Production never drains; fire-and-forget semantics are unchanged there.
    try:
        from app.api.routes_stages import drain_graph_tasks
        drain_graph_tasks(timeout=30.0)
    except Exception:
        pass
    object.__setattr__(cfg.settings, "workspace_dir", _orig_ws)
    object.__setattr__(cfg.settings, "database_url", _orig_db)
    object.__setattr__(cfg.settings, "data_dir", _orig_data)
    clear_services_cache()
    # Reset globals again so next test uses a fresh DB
    db_mod._engine = None
    db_mod._SessionLocal = None
    shutil.rmtree(tmp, ignore_errors=True)


def _seed_test_data():
    """Insert minimal test seed data for R6 model tests (resources, agents, skills).

    R9-3E: drop_all clears the persistent DB seed data. Tests expect
    these records to exist. We insert just enough to pass assertions.
    """
    from app.core.database import get_session
    from app.models.resource_entry import ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus
    from app.models.agent_definition import AgentDefinition, AgentType, AgentCategory, DefinitionStatus
    from app.models.skill_definition import SkillDefinition, SkillSeries, SkillCategory, SkillStatus
    import uuid

    db = get_session()
    try:
        # Seed 24+ resources covering multiple types
        rtypes = [ResourceType.agent, ResourceType.skill, ResourceType.tool,
                   ResourceType.hook, ResourceType.case, ResourceType.knowledge,
                   ResourceType.template, ResourceType.mcp]
        for i in range(30):
            rt = rtypes[i % len(rtypes)]
            # Case resources must be read_only (never executable)
            # Knowledge resources must be read_only/local_existing/externally_available
            if rt == ResourceType.case:
                st = ResourceStatus.read_only
            elif rt == ResourceType.knowledge:
                st = ResourceStatus.read_only
            else:
                st = ResourceStatus.active
            db.add(ResourceEntry(
                resource_id=str(uuid.uuid4()), name=f"Test Resource {i}",
                resource_type=rt, source_type=SourceType.user_provided,
                source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L0,
                status=st, description=f"Test {i}",
            ))
        # Seed agents of different types
        atypes = [AgentType.node_worker, AgentType.acceptance, AgentType.auto_review,
                   AgentType.expert, AgentType.conversation_gate]
        for i, at in enumerate(atypes):
            db.add(AgentDefinition(
                agent_id=str(uuid.uuid4()), agent_type=at, category=AgentCategory.system,
                name=f"Test {at.value}", status=DefinitionStatus.active,
                responsibilities=f"Test agent {i}", forbidden="",
            ))
        # Seed 24+ P-series skills
        scats = [SkillCategory.common, SkillCategory.p0, SkillCategory.p1, SkillCategory.p2,
                  SkillCategory.p3, SkillCategory.p4, SkillCategory.p5, SkillCategory.p6]
        for i in range(30):
            db.add(SkillDefinition(
                skill_id=str(uuid.uuid4()), name=f"Test P-Skill {i}",
                series=SkillSeries.P, category=scats[i % len(scats)],
                status=SkillStatus.active if i < 25 else SkillStatus.planned,
                description=f"Test skill {i}",
            ))
        # Seed a few R-series skills
        for i in range(3):
            db.add(SkillDefinition(
                skill_id=str(uuid.uuid4()), name=f"Test R-Skill {i}",
                series=SkillSeries.R, category=SkillCategory.other,
                status=SkillStatus.active, description=f"R skill {i}",
            ))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client(isolated_data):
    """FastAPI TestClient with isolated services."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
