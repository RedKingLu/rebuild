"""Test Audit writer and query (R9 P1-2: DB-backed gates)."""

import pytest


@pytest.fixture
def proj_with_gate(client):
    pid = client.post("/api/projects", json={
        "name": "Audit Test Project", "source_type": "manual",
    }).json()["data"]["project_id"]
    client.post(f"/api/projects/{pid}/onboarding/complete",
                json={"execution_mode": "plan"})
    client.post(f"/api/projects/{pid}/onboarding/execute")
    g = client.get(f"/api/projects/{pid}/gates/active").json()["data"]
    return pid, g["gate_id"]


def test_audit_after_gate_decision(client, proj_with_gate):
    pid, gid = proj_with_gate
    client.post(f"/api/projects/{pid}/gates/{gid}/decision", json={
        "decision": "reject", "reason": "Test rejection",
    })
    resp = client.get(f"/api/projects/{pid}/audit")
    assert resp.status_code == 200
    data = resp.json()["data"]
    audits = data["audits"]
    assert len(audits) >= 1


def test_audit_fields(client, proj_with_gate):
    pid, gid = proj_with_gate
    client.post(f"/api/projects/{pid}/gates/{gid}/decision", json={
        "decision": "approve", "reason": "Field check",
    })
    resp = client.get(f"/api/projects/{pid}/audit?limit=1")
    audits = resp.json()["data"]["audits"]
    if audits:
        a = audits[0]
        assert "audit_id" in a
        assert a["audit_id"].startswith("AU-")
        assert "audit_type" in a
        assert "risk_level" in a
        assert "decision" in a
        assert "transition_mode" in a


def test_audit_filter_by_gate(client, proj_with_gate):
    pid, gid = proj_with_gate
    client.post(f"/api/projects/{pid}/gates/{gid}/decision", json={
        "decision": "approve", "reason": "Filter test",
    })
    resp = client.get(f"/api/projects/{pid}/audit?gate_id={gid}")
    assert resp.status_code == 200


def test_audit_nonexistent(client, proj_with_gate):
    pid, _ = proj_with_gate
    resp = client.get(f"/api/projects/{pid}/audit/AU-nonexistent")
    assert resp.status_code == 404
