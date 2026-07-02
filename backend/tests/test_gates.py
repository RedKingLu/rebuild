"""Test Gate API and mandatory Audit writing (R9 P1-2: DB-backed)."""

import pytest


@pytest.fixture
def proj_with_gate(client):
    """Create a project with a real gate."""
    pid = client.post("/api/projects", json={
        "name": "Gate Test Project", "source_type": "manual",
    }).json()["data"]["project_id"]
    # Complete onboarding + agent execute to create a gate
    client.post(f"/api/projects/{pid}/onboarding/complete",
                json={"execution_mode": "plan"})
    client.post(f"/api/projects/{pid}/onboarding/execute")
    g = client.get(f"/api/projects/{pid}/gates/active").json()["data"]
    return pid, g["gate_id"], g.get("run_id", "")


def test_list_gates(client, proj_with_gate):
    pid, gid, _ = proj_with_gate
    resp = client.get(f"/api/projects/{pid}/gates")
    assert resp.status_code == 200
    gates = resp.json()["data"]["gates"]
    assert len(gates) >= 1


def test_active_gate(client, proj_with_gate):
    pid, gid, _ = proj_with_gate
    resp = client.get(f"/api/projects/{pid}/gates/active")
    assert resp.status_code == 200
    gate = resp.json()["data"]
    assert gate is not None
    assert gate["gate_id"] == gid
    assert gate["gate_status"] == "waiting_decision"


def test_gate_graph_capability_is_real_probe(client, proj_with_gate):
    """R10-5 P2-C: Gate.graph_capability_status must be a real probe (live/degraded),
    never the schema default 'not_connected' (state.py 红线)."""
    pid, gid, _ = proj_with_gate
    gate = client.get(f"/api/projects/{pid}/gates/active").json()["data"]
    assert gate["graph_capability_status"] in ("live", "degraded")
    assert gate["graph_capability_status"] != "not_connected"


def test_gate_decision_writes_audit(client, proj_with_gate):
    pid, gid, _ = proj_with_gate
    resp = client.post(f"/api/projects/{pid}/gates/{gid}/decision", json={
        "decision": "approve", "reason": "Test approval",
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["gate"]["gate_status"] == "approved"
    assert data["gate"]["decision"] == "approve"
    assert data["audit"] is not None
    assert data["audit"]["audit_type"] == "gate_decision"
    assert data["audit"]["decision"] == "approve"


def test_policy_check_real(client, proj_with_gate):
    pid, _, _ = proj_with_gate
    resp = client.post(f"/api/projects/{pid}/policy/check", json={
        "action_type": "test_action", "risk_level": "L2", "context": {"mode": "plan"},
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["allowed"] is True
    assert data["source_status"] == "real"


def test_risk_assess_real(client, proj_with_gate):
    pid, _, _ = proj_with_gate
    resp = client.post(f"/api/projects/{pid}/risk/assess", json={
        "action_type": "test_action", "target": "test", "context": {},
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    # R9-5-7 T14: risk is now derived from the single-source action→risk map
    # (mode_policy.risk_for_action) instead of hardcoded L1. An unknown action
    # type falls to the moderate default L2; source_status is "real".
    assert data["risk_level"] == "L2"
    assert data["source_status"] == "real"


def test_nonexistent_gate(client, proj_with_gate):
    pid, _, _ = proj_with_gate
    resp = client.post(f"/api/projects/{pid}/gates/nonexistent/decision", json={
        "decision": "approve", "reason": "test",
    })
    assert resp.status_code == 404
