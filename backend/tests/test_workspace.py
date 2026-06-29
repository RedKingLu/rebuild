"""Test Workspace aggregate API — uses DB-backed project."""

import pytest


@pytest.fixture
def project_id(client):
    """Create a DB-backed project so workspace routes resolve."""
    resp = client.post("/api/projects", json={
        "name": "Workspace Test Project",
        "description": "Project for workspace tests",
        "source_type": "local_dir",
    })
    assert resp.status_code == 200
    return resp.json()["data"]["project_id"]


def test_workspace_aggregate(client, project_id):
    resp = client.get(f"/api/projects/{project_id}/workspace")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["project"] is not None
    assert data["project"]["name"] == "Workspace Test Project"
    assert "stage_statuses" in data
    # R9-5-1 阶段D: real LangGraph orchestration → capability probe is live, not_connected retired
    assert data["graph_status"]["graph_capability_status"] in ("live", "degraded")
    assert data["graph_status"]["transition_mode"] == "langgraph"
    # Meta — WP-4 F-4: real backend, source_status must be "real" (not "mock")
    assert data["meta"]["source_status"] == "real"


def test_workspace_nonexistent_project(client):
    resp = client.get("/api/projects/nonexistent/workspace")
    assert resp.status_code == 404


def test_workspace_has_all_sections(client, project_id):
    resp = client.get(f"/api/projects/{project_id}/workspace")
    assert resp.status_code == 200
    data = resp.json()["data"]
    required_sections = [
        "project", "active_run", "stage_statuses", "active_gate",
        "pending_gates", "recent_artifacts", "pending_evidence_gaps",
        "recent_traces", "recent_audits", "file_index", "graph_status", "meta",
    ]
    for section in required_sections:
        assert section in data, f"Missing section: {section}"
