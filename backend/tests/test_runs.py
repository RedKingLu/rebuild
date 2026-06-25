"""Test Run API."""


def test_list_runs(client):
    resp = client.get("/api/projects/proj-001/runs")
    assert resp.status_code == 200
    data = resp.json()
    runs = data["data"]["runs"]
    assert len(runs) >= 1
    assert runs[0]["project_id"] == "proj-001"


def test_get_run(client):
    resp = client.get("/api/projects/proj-001/runs/run-001")
    assert resp.status_code == 200
    run = resp.json()["data"]
    assert run["run_goal"] == "评估技术栈迁移风险"
    assert run["run_status"] == "running"
    # R4 mock transition fields
    assert "transition_mode" in run
    assert "graph_capability_status" in run


def test_create_run(client):
    resp = client.post("/api/projects/proj-001/runs", json={
        "run_goal": "Test run",
        "mode": "plan",
    })
    assert resp.status_code == 200
    run = resp.json()["data"]
    assert run["run_status"] == "created"
    assert run["project_id"] == "proj-001"


def test_run_lifecycle_mock_transition(client):
    """Run lifecycle operations must be mock transitions."""
    # Create
    resp = client.post("/api/projects/proj-001/runs", json={"run_goal": "Lifecycle test"})
    rid = resp.json()["data"]["run_id"]

    # Start
    resp = client.post(f"/api/projects/proj-001/runs/{rid}/start")
    assert resp.status_code == 200
    assert resp.json()["data"]["run_status"] == "running"
    assert resp.json()["data"]["transition_mode"] == "mock"

    # Pause
    resp = client.post(f"/api/projects/proj-001/runs/{rid}/pause")
    assert resp.json()["data"]["run_status"] == "paused"

    # Resume
    resp = client.post(f"/api/projects/proj-001/runs/{rid}/resume", json={"decision": "approve"})
    assert resp.json()["data"]["run_status"] == "running"

    # Cancel
    resp = client.post(f"/api/projects/proj-001/runs/{rid}/cancel")
    assert resp.json()["data"]["run_status"] == "canceled"


def test_nonexistent_run(client):
    resp = client.get("/api/projects/proj-001/runs/nonexistent")
    assert resp.status_code == 404
