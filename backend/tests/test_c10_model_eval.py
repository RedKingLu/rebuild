"""R15-4-C10: AgentModelEvalResult — migration + seed + API."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _restore_settings():
    """Restore global settings mutated by _setup / inline blocks in this module.

    These tests redirect the frozen global cfg.settings (data_dir / source_dir /
    database_url) via object.__setattr__ but had no teardown, leaving source_dir
    pointing at a deleted tmp_path after the test — polluting later tests that read
    settings.source_path (e.g. skill_loader disk fallback). Mirror conftest
    isolated_data's _orig_* save/restore pattern: snapshot before the test body
    mutates, restore on teardown. Runs after conftest's autouse isolated_data
    (conftest fixtures set up first, tear down last), so the chain restores
    correctly to true originals.
    """
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    _orig_data = cfg.settings.data_dir
    _orig_source = cfg.settings.source_dir
    _orig_db = cfg.settings.database_url
    yield
    object.__setattr__(cfg.settings, "data_dir", _orig_data)
    object.__setattr__(cfg.settings, "source_dir", _orig_source)
    object.__setattr__(cfg.settings, "database_url", _orig_db)
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()


def _setup(monkeypatch, tmp_path):
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "data"))
    object.__setattr__(cfg.settings, "source_dir", str(tmp_path / "source"))
    object.__setattr__(cfg.settings, "database_url", f"sqlite:///{tmp_path / 'rebuild.db'}")
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()


def test_migration_creates_table(tmp_path, monkeypatch):
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    object.__setattr__(cfg.settings, "database_url", f"sqlite:///{tmp_path / 'rebuild.db'}")
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "data"))
    object.__setattr__(cfg.settings, "source_dir", str(tmp_path / "source"))
    db_mod._engine = None; db_mod._SessionLocal = None; clear_services_cache()
    import sqlite3
    from alembic.config import Config
    from alembic import command
    c = sqlite3.connect(tmp_path / "rebuild.db")
    c.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    c.execute("INSERT INTO alembic_version (version_num) VALUES ('7c407d04e07b')")
    c.commit(); c.close()
    alembic_cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(alembic_cfg, "head")
    import sqlite3 as _sq
    c2 = _sq.connect(tmp_path / "rebuild.db")
    tables = {r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "agent_model_eval" in tables, tables
    c2.close()


def test_seed_and_api(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    try:
        from app.api.routes_model_eval import seed_exemplar_evaluations
        assert seed_exemplar_evaluations(db) == 2
        assert seed_exemplar_evaluations(db) == 0  # idempotent
    finally:
        db.close()
    from app.main import app
    c = TestClient(app)
    r = c.get("/api/model-evaluations")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 2
    # every row must expose source/eval_method/limitations/sample_count (red line #10/#11)
    for e in data["evaluations"]:
        assert "source" in e and "eval_method" in e and "limitations" in e and "sample_count" in e
        assert e["source"] and e["eval_method"] and e["limitations"]
    # filter by model
    r2 = c.get("/api/model-evaluations?model_id=glm-5.2")
    assert r2.status_code == 200
    assert r2.json()["data"]["total"] >= 1


def test_import_api(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    body = {"evaluations": [{
        "eval_id": "imp-1", "model_id": "acme/custom", "task_type": "code_migration",
        "scenario": "S→T", "metric": "pass_rate", "score": 0.9, "sample_count": 20,
        "eval_method": "导入的第三方评测（非平台自动评测）", "source": "社区",
        "limitations": "样本有限，不代表模型全局能力。",
    }]}
    r = c.post("/api/model-evaluations/import", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["imported"] == 1
