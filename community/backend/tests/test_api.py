"""R15-4-C3 community-backend API smoke test against a real temp DB.

Starts the FastAPI app with an isolated COMMUNITY_DATABASE_URL and exercises
all 8 endpoints + the real download→sha256 verification flow. Non-mock.
"""
from __future__ import annotations

import hashlib
import tempfile
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TMP = Path(tempfile.mkdtemp(prefix="commtest_"))


@pytest.fixture()
def client(tmp_path_factory, monkeypatch):
    # isolated DB + package dir per test. Env is set BEFORE the first import so
    # the DeclarativeBase identity is consistent (reload would break it).
    data = tmp_path_factory.mktemp("comm")
    monkeypatch.setenv("COMMUNITY_DATA_DIR", str(data))
    monkeypatch.setenv("COMMUNITY_DATABASE_URL", f"sqlite:///{data / 'community.db'}")

    import app.main as main_mod  # noqa: E402  (after env set)

    with TestClient(main_mod.app) as c:
        yield c


def test_status_ok(client):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["resource_count"] >= 1
    assert body["model_count"] >= 1


def test_news_list(client):
    r = client.get("/news")
    assert r.status_code == 200
    assert len(r.json()["news"]) >= 1


def test_resources_list_open_vsx_paging(client):
    r = client.get("/resources")
    assert r.status_code == 200
    body = r.json()
    assert "totalSize" in body and "offset" in body and "resources" in body
    assert body["totalSize"] >= 1


def test_resources_filter_by_type(client):
    r = client.get("/resources", params={"type": "case"})
    assert r.status_code == 200
    for card in r.json()["resources"]:
        assert card["resource_type"] == "case"


def test_resource_detail_and_manifest(client):
    r = client.get("/resources")
    rid = r.json()["resources"][0]["id"]
    detail = client.get(f"/resources/{rid}")
    assert detail.status_code == 200
    assert "readme" in detail.json()
    manifest = client.get(f"/resources/{rid}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["checksum_sha256"]


def test_download_returns_real_zip_with_valid_sha256(client):
    """Download a real package and verify its sha256 matches the header/db."""
    rid = client.get("/resources").json()["resources"][0]["id"]
    expected = client.get(f"/resources/{rid}/manifest").json()["checksum_sha256"]
    dl = client.get(f"/resources/{rid}/download")
    assert dl.status_code == 200, dl.text
    header_sha = dl.headers.get("X-Checksum-SHA256")
    assert header_sha == expected, f"header {header_sha!r} != expected {expected!r}"
    actual = hashlib.sha256(dl.content).hexdigest()
    assert actual == expected, "downloaded bytes sha256 mismatch (P0: checksum must verify)"


def test_models_and_evaluations(client):
    models = client.get("/models")
    assert models.status_code == 200
    assert "totalSize" in models.json()
    evals = client.get("/evaluations")
    assert evals.status_code == 200
    assert len(evals.json()["evaluations"]) >= 1
    # Every eval must expose method/limitations (R15 red line #10/#11 display)
    for e in evals.json()["evaluations"]:
        assert "eval_method" in e and "limitations" in e


def test_unknown_resource_404(client):
    assert client.get("/resources/does-not-exist").status_code == 404
    assert client.get("/resources/does-not-exist/download").status_code == 404
