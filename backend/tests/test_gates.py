"""Test Gate API and mandatory Audit writing."""


def test_list_gates(client):
    resp = client.get("/api/projects/proj-001/gates")
    assert resp.status_code == 200
    gates = resp.json()["data"]["gates"]
    assert len(gates) >= 1


def test_active_gate(client):
    resp = client.get("/api/projects/proj-001/gates/active")
    assert resp.status_code == 200
    gate = resp.json()["data"]
    assert gate is not None
    assert gate["gate_id"] == "gate-001"
    assert gate["gate_status"] == "waiting_decision"


def test_gate_decision_writes_audit(client):
    """D-034: Gate decision MUST write an Audit entry."""
    resp = client.post("/api/projects/proj-001/gates/gate-001/decision", json={
        "decision": "approve",
        "reason": "Test approval",
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Gate should be updated
    assert data["gate"]["gate_status"] == "approved"
    assert data["gate"]["decision"] == "approve"
    # Audit MUST be present
    assert data["audit"] is not None
    assert data["audit"]["audit_id"].startswith("AU-")
    assert data["audit"]["decision"] == "approve"
    assert data["audit"]["audit_type"] == "gate_decision"
    assert data["audit"]["persistence"] == "file+memory"


def test_policy_check_mock(client):
    resp = client.post("/api/projects/proj-001/policy/check", json={
        "action_type": "test_action",
        "risk_level": "L2",
        "context": {},
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["allowed"] is True  # R4 always allows
    assert data["source_status"] == "mock"


def test_risk_assess_mock(client):
    resp = client.post("/api/projects/proj-001/risk/assess", json={
        "action_type": "test_action",
        "target": "test",
        "context": {},
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["risk_level"] == "L0"
    assert data["source_status"] == "mock"


def test_nonexistent_gate(client):
    resp = client.post("/api/projects/proj-001/gates/nonexistent/decision", json={
        "decision": "approve",
        "reason": "test",
    })
    assert resp.status_code == 404
