"""pytest fixtures — isolated data directory + DB per test (absorbed from V26.0 pattern)."""

import pytest
import tempfile
import os

from app.core.config import Settings
from app.dependencies import clear_services_cache, get_services
from app.core.database import _engine, _SessionLocal


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
    clear_services_cache()
    svc = get_services(settings)
    # workspace_service / trace_writer / audit_writer read the GLOBAL settings
    # singleton directly (not the injected one), so isolate it too — otherwise
    # tests pollute the real 工作区/ tree. object.__setattr__ bypasses frozen.
    import app.core.config as cfg
    _orig_ws = cfg.settings.workspace_dir
    object.__setattr__(cfg.settings, "workspace_dir", ws_tmp)
    # R9-3A: Drop + recreate tables to pick up new columns (SQLite create_all
    # won't alter existing tables, so old project table lacks coding_agent_ref).
    import app.models  # noqa: F401
    from app.models.base import Base
    from app.core.database import get_engine
    Base.metadata.drop_all(bind=get_engine())
    Base.metadata.create_all(bind=get_engine())
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
    object.__setattr__(cfg.settings, "workspace_dir", _orig_ws)
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
