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


def test_models_real_after_r5(client):
    """R5 起模型域已真实化：/api/model/providers 返回真实 Provider（configured/available）。

    （此前 R4 期断言 not_connected/future 已随 R5-3 模型域真实化而更新——R5-4 修复 C-1。）
    """
    resp = client.get("/api/model/providers")
    assert resp.status_code == 200
    data = resp.json()
    providers = data["data"]["providers"]
    assert len(providers) >= 1
    # 真实域：每个 provider 有 credential_status 与能力标记，且不回显 Key
    for p in providers:
        assert "credential_status" in p
        assert "capability_marker" in p
        assert "api_key" not in p
    # meta 不再是 not_connected/future（模型域已真实接入）
    assert data["meta"]["source_status"] != "not_connected"
    assert data["meta"]["capability_status"] != "future"


def test_resources_connected(client):
    """R6: Resource Registry returns real data with source_status=real."""
    resp = client.get("/api/resources")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] > 0  # Seed data loaded


def test_integrations_real_after_r7(client):
    """R7: Integration endpoints are now real (replaced placeholder).

    The old /api/projects/{id}/integrations placeholder is replaced by
    /api/integrations/* real endpoints. R7 delivers Git/Remote/Feishu
    integration management with DB-backed CRUD and aggregate summary.
    R8-5 (D-076): 'execution' key renamed to 'coding_agents' to reflect
    the correct separation of code execution from AI coding agent config.
    """
    resp = client.get("/api/integrations/summary")
    assert resp.status_code == 200
    data = resp.json()
    # R7: Integration summary returns real status (not not_connected)
    assert data["meta"]["source_status"] == "real"
    assert "git" in data["data"]
    assert "remote" in data["data"]
    assert "coding_agents" in data["data"]  # R8-5: renamed from 'execution'
    assert "other" in data["data"]
