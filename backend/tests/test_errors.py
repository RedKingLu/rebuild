"""Test error responses and Problem Details format."""


def test_404_format(client):
    resp = client.get("/api/projects/nonexistent")
    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_validation_error(client):
    """Sending empty body where name is required should 422."""
    resp = client.post("/api/projects", json={})
    assert resp.status_code == 422


def test_artifacts_empty(client):
    resp = client.get("/api/projects/proj-001/artifacts")
    assert resp.status_code == 200


def test_evidence_empty(client):
    resp = client.get("/api/projects/proj-001/evidence")
    assert resp.status_code == 200


def test_models_not_connected(client):
    resp = client.get("/api/model/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["source_status"] == "not_connected"
    assert data["meta"]["capability_status"] == "future"


def test_resources_not_connected(client):
    resp = client.get("/api/resources")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["source_status"] == "not_connected"


def test_integrations_not_connected(client):
    resp = client.get("/api/projects/proj-001/integrations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["source_status"] == "not_connected"
