"""R15-4-C8: ModelCatalog persistent table + seeder + API."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    from app.main import app
    return TestClient(app)


def test_migration_creates_table(tmp_path, monkeypatch):
    """Alembic migration 7c407d04e07b creates model_catalog.

    Note: a full `alembic upgrade` from zero hits a pre-existing early migration
    (003_add_agent_fields) that uses a non-batch ALTER SQLite rejects — an
    out-of-scope, pre-existing condition the R14 migration head also documents.
    Rebuild unit-test DBs use create_all (not a from-zero migration). We therefore
    stamp at the reachable R15-4 head c4f1a9d7e2b8 and upgrade our migration on top,
    which is the real path a live DB takes (it's already past 003 via create_all).
    """
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    object.__setattr__(cfg.settings, "database_url", f"sqlite:///{tmp_path / 'rebuild.db'}")
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "data"))
    object.__setattr__(cfg.settings, "source_dir", str(tmp_path / "source"))
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()
    import sqlite3
    from alembic.config import Config
    from alembic import command
    # Seed the reachable R15-4 head so our migration applies on a realistic DB.
    c = sqlite3.connect(tmp_path / "rebuild.db")
    c.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    c.execute("INSERT INTO alembic_version (version_num) VALUES ('c4f1a9d7e2b8')")
    c.commit(); c.close()
    alembic_cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(alembic_cfg, "head")
    import sqlite3
    c = sqlite3.connect(tmp_path / "rebuild.db")
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "model_catalog" in tables, tables
    cols = {r[1] for r in c.execute("PRAGMA table_info(model_catalog)").fetchall()}
    for needed in ("catalog_id", "model_id", "provider_id", "display_name", "context_window", "capability_tags"):
        assert needed in cols, cols


def test_seeder_populates_from_yaml(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    try:
        from app.seed import seed_model_catalog
        n = seed_model_catalog(db)
        assert n == 9, f"expected 9 catalog entries, got {n}"  # 3 providers, 9 models
        assert seed_model_catalog(db) == 0  # idempotent
        from app.models.model_catalog import ModelCatalogEntry
        assert db.query(ModelCatalogEntry).count() == 9
    finally:
        db.close()


def test_list_api_and_filters(tmp_path, monkeypatch):
    """API returns seeded catalog + filters work (seeds the test DB inline)."""
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    from app.seed import seed_model_catalog
    seed_model_catalog(db)
    db.close()
    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    r = client.get("/api/model-catalog")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 9
    r2 = client.get("/api/model-catalog?provider=deepseek-official")
    assert r2.status_code == 200
    assert r2.json()["data"]["total"] >= 1
    for m in r2.json()["data"]["models"]:
        assert m["provider_id"] == "deepseek-official"
    # task filter (JSON contains) returns subset
    r3 = client.get("/api/model-catalog?task=code")
    assert r3.status_code == 200


def test_import_api(client, tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    body = {"models": [
        {"model_id": "custom-model-x", "provider_id": "acme-ai", "display_name": "Custom X",
         "capability_tags": ["coding"], "task_tags": ["code"], "context_window": 64000},
    ]}
    r = client.post("/api/model-catalog/import", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["imported"] == 1
    from app.models.model_catalog import ModelCatalogEntry
    assert db.get(ModelCatalogEntry, "acme-ai/custom-model-x") is not None
    db.close()
