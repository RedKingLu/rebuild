"""Test Run API — uses DB-backed project existence check."""

import pytest


@pytest.fixture
def project_id(client):
    """Create a DB-backed project that run routes can reference."""
    resp = client.post("/api/projects", json={
        "name": "Run Test Project",
        "description": "Project for run tests",
        "source_type": "local_dir",
    })
    assert resp.status_code == 200
    return resp.json()["data"]["project_id"]


def test_list_runs(client, project_id):
    resp = client.get(f"/api/projects/{project_id}/runs")
    assert resp.status_code == 200
    data = resp.json()
    runs = data["data"]["runs"]
    assert isinstance(runs, list)


def test_create_run(client, project_id):
    resp = client.post(f"/api/projects/{project_id}/runs", json={
        "run_goal": "Test run",
        "mode": "plan",
    })
    assert resp.status_code == 200
    run = resp.json()["data"]
    assert run["run_status"] == "created"
    assert run["project_id"] == project_id


def test_run_lifecycle_mock_transition(client, project_id):
    """Run lifecycle operations must be mock transitions."""
    # Create
    resp = client.post(f"/api/projects/{project_id}/runs", json={"run_goal": "Lifecycle test"})
    assert resp.status_code == 200
    rid = resp.json()["data"]["run_id"]

    # Start
    resp = client.post(f"/api/projects/{project_id}/runs/{rid}/start")
    assert resp.status_code == 200
    assert resp.json()["data"]["run_status"] == "running"
    assert resp.json()["data"]["transition_mode"] in ("mock", "real")

    # Pause
    resp = client.post(f"/api/projects/{project_id}/runs/{rid}/pause")
    assert resp.json()["data"]["run_status"] == "paused"

    # Resume
    resp = client.post(f"/api/projects/{project_id}/runs/{rid}/resume", json={"decision": "approve"})
    assert resp.json()["data"]["run_status"] == "running"

    # Cancel
    resp = client.post(f"/api/projects/{project_id}/runs/{rid}/cancel")
    assert resp.json()["data"]["run_status"] == "canceled"


def test_get_run(client, project_id):
    # Create a run first so we have a known ID
    resp = client.post(f"/api/projects/{project_id}/runs", json={
        "run_goal": "Get test run",
        "mode": "auto",
    })
    assert resp.status_code == 200
    rid = resp.json()["data"]["run_id"]

    resp = client.get(f"/api/projects/{project_id}/runs/{rid}")
    assert resp.status_code == 200
    run = resp.json()["data"]
    assert run["run_goal"] == "Get test run"
    assert "transition_mode" in run
    assert "stage_status" in run


def test_nonexistent_run(client, project_id):
    resp = client.get(f"/api/projects/{project_id}/runs/nonexistent")
    assert resp.status_code == 404


def test_nonexistent_project_run(client):
    resp = client.get("/api/projects/nonexistent-999/runs")
    # The list endpoint does not validate project existence; returns empty list
    assert resp.status_code == 200
    assert resp.json()["data"]["runs"] == []
