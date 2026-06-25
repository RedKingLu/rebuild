"""Test health, version, meta endpoints."""


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["platform"] == "rebuild"
    assert data["version"] == "V26.1.1"
    assert data["r_stage"] == "R4"


def test_version(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == "rebuild"
    assert data["version"] == "V26.1.1"


def test_meta(client):
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert "enums" in data
    assert "r_stages" in data["enums"]
    assert "p_stages" in data["enums"]
    assert data["source_status"] == "mock"


def test_docs_available(client):
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_openapi_schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})
    # At minimum: /api/health, /api/version, /api/meta, /api/projects
    assert len(paths) >= 10, f"Expected >=10 paths, got {len(paths)}"
