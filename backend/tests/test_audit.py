"""Test Audit writer and query."""


def test_audit_after_gate_decision(client):
    """Gate decision should produce an audit entry."""
    # Trigger gate decision
    client.post("/api/projects/proj-001/gates/gate-001/decision", json={
        "decision": "reject",
        "reason": "Test rejection",
    })
    # Query audits
    resp = client.get("/api/projects/proj-001/audit")
    assert resp.status_code == 200
    data = resp.json()["data"]
    audits = data["audits"]
    assert len(audits) >= 1
    assert data["persistence"] == "file+memory"


def test_audit_fields(client):
    client.post("/api/projects/proj-001/gates/gate-001/decision", json={
        "decision": "approve",
        "reason": "Field check",
    })
    resp = client.get("/api/projects/proj-001/audit?limit=1")
    audits = resp.json()["data"]["audits"]
    if audits:
        a = audits[0]
        assert "audit_id" in a
        assert a["audit_id"].startswith("AU-")
        assert "audit_type" in a
        assert "risk_level" in a
        assert "decision" in a
        assert "transition_mode" in a
        assert a["transition_mode"] == "mock"
        assert a["persistence"] == "file+memory"


def test_audit_filter_by_gate(client):
    client.post("/api/projects/proj-001/gates/gate-001/decision", json={
        "decision": "approve",
        "reason": "Filter test",
    })
    resp = client.get("/api/projects/proj-001/audit?gate_id=gate-001")
    assert resp.status_code == 200


def test_audit_nonexistent(client):
    resp = client.get("/api/projects/proj-001/audit/AU-nonexistent")
    assert resp.status_code == 404
