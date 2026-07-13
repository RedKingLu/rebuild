"""Test Trace writer and query — uses DB-backed project."""

import pytest


@pytest.fixture
def project_id(client):
    """Create a DB-backed project so trace endpoints resolve."""
    resp = client.post("/api/projects", json={
        "name": "Trace Test Project",
        "description": "Project for trace tests",
        "source_type": "local_dir",
    })
    assert resp.status_code == 200
    return resp.json()["data"]["project_id"]


def test_trace_writer_records_requests(client, project_id):
    """Each API request should create a trace via the trace_writer."""
    # Make some requests that write traces
    client.get("/api/projects")
    client.get(f"/api/projects/{project_id}")

    # Query traces
    resp = client.get(f"/api/projects/{project_id}/trace")
    assert resp.status_code == 200
    data = resp.json()["data"]
    traces = data["traces"]
    assert len(traces) >= 1
    assert data["persistence"] == "file+memory"


def test_trace_has_required_fields(client, project_id):
    client.get(f"/api/projects/{project_id}")
    resp = client.get(f"/api/projects/{project_id}/trace?limit=1")
    assert resp.status_code == 200
    traces = resp.json()["data"]["traces"]
    if traces:
        t = traces[0]
        assert "trace_id" in t
        assert "trace_type" in t
        assert "summary" in t
        assert "graph_status" in t
        assert "transition_mode" in t
        assert t["graph_status"] == "not_connected"
        assert t["transition_mode"] == "unknown"
        assert t["persistence"] == "file+memory"


def test_trace_filter_by_type(client, project_id):
    client.get(f"/api/projects/{project_id}")
    client.get(f"/api/projects/{project_id}")
    resp = client.get(f"/api/projects/{project_id}/trace?trace_type=state_change&limit=50")
    assert resp.status_code == 200


def test_trace_nonexistent(client, project_id):
    resp = client.get(f"/api/projects/{project_id}/trace/trace-nonexistent")
    assert resp.status_code == 404
