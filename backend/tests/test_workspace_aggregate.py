"""R10-5 P1-A: /workspace aggregate must not 500 when uncertainty_manifest.json
has evidence_gaps. Regression for EvidenceGapResponse schema mismatch.
"""

import json

import pytest

from app.services.workspace_service import workspace_path


@pytest.fixture
def project_id(client):
    resp = client.post("/api/projects", json={
        "name": "Gap Workspace Project",
        "description": "Project with evidence gaps",
        "source_type": "local_dir",
    })
    assert resp.status_code == 200
    return resp.json()["data"]["project_id"]


def _write_manifest_with_gaps(pid: str):
    art_dir = workspace_path(pid) / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    manifest = art_dir / "uncertainty_manifest.json"
    manifest.write_text(json.dumps({
        "evidence_gaps": [
            {"item": 5, "type": "framework_unknown",
             "detail": "No framework confidently identified from project files"},
            {"item": 6, "type": "build_system_unknown", "detail": "No build file detected"},
        ],
        "total_gaps": 2,
    }, ensure_ascii=False), encoding="utf-8")


def test_workspace_aggregate_with_evidence_gaps(client, project_id):
    """Source project past P1 (uncertainty_manifest with framework_unknown gap)
    must return 200 with a schema-valid pending_evidence_gaps list."""
    _write_manifest_with_gaps(project_id)
    resp = client.get(f"/api/projects/{project_id}/workspace")
    assert resp.status_code == 200, resp.text
    gaps = resp.json()["data"]["pending_evidence_gaps"]
    assert len(gaps) == 2
    for g in gaps:
        # schema-required fields present (previously missing → 500)
        assert g["gap_id"]
        assert "evidence_type" in g
        assert "description" in g
        assert "blocking" in g
        assert "stage" in g
        assert g["source_status"] == "real"
    types = {g["evidence_type"] for g in gaps}
    assert "framework_unknown" in types
    assert "build_system_unknown" in types


def test_evidence_gaps_endpoint_with_gaps(client, project_id):
    """The /evidence-gaps route returns the same aligned DTO shape."""
    _write_manifest_with_gaps(project_id)
    resp = client.get(f"/api/projects/{project_id}/evidence-gaps")
    assert resp.status_code == 200, resp.text
    gaps = resp.json()["data"]["gaps"]
    assert len(gaps) == 2
    assert gaps[0]["gap_id"].startswith("gap-")
    assert gaps[0]["evidence_type"] == "framework_unknown"
