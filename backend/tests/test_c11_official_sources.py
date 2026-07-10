"""R15-4-C11: OfficialSource empty seam — returns not_connected, never fabricated."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "data"))
    object.__setattr__(cfg.settings, "source_dir", str(tmp_path / "source"))
    object.__setattr__(cfg.settings, "database_url", f"sqlite:///{tmp_path / 'rebuild.db'}")
    db_mod._engine = None; db_mod._SessionLocal = None; clear_services_cache()
    from app.main import app
    return TestClient(app)


def test_official_sources_returns_not_connected(client):
    r = client.get("/api/official-sources")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # seam is empty → no connected sources, never fabricated
    assert data["connected_count"] == 0
    assert data["state"] in ("not_connected", "partial")
    for s in data["sources"]:
        assert s["connected"] is False
        assert s["state"] == "not_connected"


def test_official_source_registry_is_empty():
    from app.services.official_source import OFFICIAL_SOURCE_REGISTRY, list_official_sources
    assert OFFICIAL_SOURCE_REGISTRY == {}
    assert list_official_sources() == []
