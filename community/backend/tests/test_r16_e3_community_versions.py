"""R16-B E3: community-backend /resources/{id}/versions + per-version manifest.

Uses the proven test_api.py fixture pattern (env set before import, TestClient triggers
startup → isolated temp DB with seeded multi-version resources).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path_factory, monkeypatch):
    data = tmp_path_factory.mktemp("comm")
    monkeypatch.setenv("COMMUNITY_DATA_DIR", str(data))
    monkeypatch.setenv("COMMUNITY_DATABASE_URL", f"sqlite:///{data / 'community.db'}")
    import app.main as main_mod
    with TestClient(main_mod.app) as c:
        yield c


def _first_multi_version_resource(client):
    """Pick a seeded resource that has more than one version."""
    r = client.get("/resources")
    assert r.status_code == 200, r.text
    for res in r.json()["resources"]:
        if res["id"] == "case-oracle-to-dm-migration":  # versions=["1.0.0","1.1.0","1.2.0"]
            return res["id"]
    return r.json()["resources"][0]["id"] if r.json()["resources"] else None


def test_versions_list_returns_all_versions_with_note(client):
    rid = _first_multi_version_resource(client)
    assert rid, "no seeded resource"
    r = client.get(f"/resources/{rid}/versions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resource_id"] == rid
    assert len(body["versions"]) >= 1
    assert body["current_version"]
    # note must disclose traceability-only honesty (no per-version binaries)
    assert "追溯" in body.get("note", "") or "当前" in body.get("note", "")
    # current version's manifest_ref is populated; others empty
    for v in body["versions"]:
        if v["version"] == body["current_version"]:
            assert v["manifest_ref"], "current version must have a manifest ref"
        else:
            assert v["manifest_ref"] == "", "historical versions must not claim manifest"


def test_version_manifest_404_for_non_current(client):
    rid = _first_multi_version_resource(client)
    assert rid
    # oracle-to-dm current=1.2.0; 1.0.0 should 404
    r = client.get(f"/resources/{rid}/versions/1.0.0/manifest")
    assert r.status_code == 404, f"historical version manifest must 404, got {r.status_code}: {r.text}"


def test_version_manifest_200_for_current(client):
    rid = _first_multi_version_resource(client)
    assert rid
    # fetch current version then request that exact manifest
    vlist = client.get(f"/resources/{rid}/versions").json()
    cur = vlist["current_version"]
    r = client.get(f"/resources/{rid}/versions/{cur}/manifest")
    assert r.status_code == 200, f"current version manifest must 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["version"] == cur
    assert body["resource_id"] == rid


def test_versions_list_404_for_unknown(client):
    r = client.get("/resources/no-such-id/versions")
    assert r.status_code == 404, r.text
