"""Test Workspace aggregate API."""


def test_workspace_aggregate(client):
    resp = client.get("/api/projects/proj-001/workspace")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["project"] is not None
    assert data["project"]["name"] == "MicroOA 信创迁移"
    assert data["active_run"] is not None
    assert data["active_run"]["run_status"] == "running"
    assert "stage_statuses" in data
    # Graph status must be not_connected
    assert data["graph_status"]["graph_capability_status"] == "not_connected"
    assert data["graph_status"]["transition_mode"] == "mock"
    # Meta
    assert data["meta"]["source_status"] == "mock"


def test_workspace_nonexistent_project(client):
    resp = client.get("/api/projects/nonexistent/workspace")
    assert resp.status_code == 404


def test_workspace_has_all_sections(client):
    resp = client.get("/api/projects/proj-001/workspace")
    data = resp.json()["data"]
    required_sections = [
        "project", "active_run", "stage_statuses", "active_gate",
        "pending_gates", "recent_artifacts", "pending_evidence_gaps",
        "recent_traces", "recent_audits", "file_index", "graph_status", "meta",
    ]
    for section in required_sections:
        assert section in data, f"Missing section: {section}"
