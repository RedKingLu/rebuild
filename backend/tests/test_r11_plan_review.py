"""R11-3 B-PLAN-1 regression: Plan/Manual modes must produce a plan for review
BEFORE executing stage actions; the three modes must be behaviorally distinct.

D-025/D-026: Manual/Plan review the Stage Plan before actions run; Auto self-reviews
and executes. Before this fix, /onboarding/complete executed + created a promotion
Gate immediately regardless of mode (no pre-execution plan review). Now the LangGraph
work node pauses at a plan_review Gate for manual/plan; source materialization (the P0
action) is deferred to after plan approval.

Uses `with TestClient(app)` + tmp checkpoint (test_graph_api / test_w9w10 pattern) so
the graph checkpoint thread survives across requests for the plan-approval resume.
No mock, no fabricated pass — asserts on the live gate type + real artifacts on disk.
"""

import pytest

from app.services import workspace_service


@pytest.fixture(autouse=True)
def _reset_graph_singletons():
    import asyncio
    from app.graph.checkpoint import reset_checkpointer_for_test
    from app.graph.runtime import reset_flow_runtime_for_test
    asyncio.run(reset_checkpointer_for_test())
    reset_flow_runtime_for_test()
    yield
    asyncio.run(reset_checkpointer_for_test())
    reset_flow_runtime_for_test()


def _client_with_graph(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.graph.stage_handlers as sh
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path",
                        lambda: tmp_path / "graph_checkpoints.sqlite")
    sh._bootstrapped = False  # force lifespan to re-register real handlers
    return TestClient(app)


def _make_project(c, name):
    r = c.post("/api/projects", json={"name": name, "source_type": "manual",
                                      "source_config": {}})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["project_id"]


def _active_gate(c, pid):
    return c.get(f"/api/projects/{pid}/workspace").json()["data"].get("active_gate")


def _artifacts(pid):
    return workspace_service.workspace_path(pid) / "artifacts"


def test_plan_mode_pauses_at_plan_review_before_execution(tmp_path, monkeypatch, isolated_data):
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = _make_project(c, "plan-mode")
        d = c.post(f"/api/projects/{pid}/onboarding/complete",
                   json={"execution_mode": "plan"}).json()["data"]

        # 1) first gate is a plan_review gate — NOT a promotion gate
        gate = _active_gate(c, pid)
        assert gate is not None, "plan mode must produce a gate to review"
        assert gate["gate_type"] == "plan_review", f"expected plan_review, got {gate['gate_type']}"

        # 2) the plan is available for review, but execution has NOT happened yet
        art = _artifacts(pid)
        assert (art / "p0_start_plan.json").exists(), "plan (起始计划) must be produced for review"
        assert "p0_start_plan.json" in " ".join(gate.get("artifact_refs") or [])
        assert not (art / "p0_acceptance.json").exists(), \
            "stage actions must NOT run before the plan is approved (审核通过后才执行动作)"

        # 3) approve the plan → graph resumes → executes → pauses at the promotion gate
        run_id = d["run_id"]
        r = c.post(f"/api/projects/{pid}/runs/{run_id}/stages/p0/promotion-decision",
                   json={"decision": "approve", "reason": "approve plan"})
        assert r.status_code == 200, r.text

        gate2 = _active_gate(c, pid)
        assert gate2 is not None and gate2["gate_type"] == "stage_promotion", \
            f"after plan approval the promotion gate should appear, got {gate2}"
        assert (art / "p0_acceptance.json").exists(), \
            "stage actions (and the real acceptance report) must run after plan approval"


def test_manual_mode_also_reviews_plan_first(tmp_path, monkeypatch, isolated_data):
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = _make_project(c, "manual-mode")
        c.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "manual"})
        gate = _active_gate(c, pid)
        assert gate is not None and gate["gate_type"] == "plan_review", \
            f"manual mode must review the plan first, got {gate}"
        assert not (_artifacts(pid) / "p0_acceptance.json").exists()


def test_auto_mode_skips_plan_review_and_executes(tmp_path, monkeypatch, isolated_data):
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = _make_project(c, "auto-mode")
        c.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
        gate = _active_gate(c, pid)
        # Auto self-reviews the plan → no user plan gate → straight to the promotion gate
        assert gate is not None and gate["gate_type"] == "stage_promotion", \
            f"auto mode must NOT insert a plan_review gate, got {gate}"
        assert (_artifacts(pid) / "p0_acceptance.json").exists(), \
            "auto mode executes stage actions immediately"


def test_three_modes_are_behaviorally_distinct(tmp_path, monkeypatch, isolated_data):
    """One assertion set proving Manual/Plan differ from Auto at the P0 entry."""
    with _client_with_graph(tmp_path, monkeypatch) as c:
        gate_types = {}
        for mode in ("manual", "plan", "auto"):
            pid = _make_project(c, f"distinct-{mode}")
            c.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": mode})
            g = _active_gate(c, pid)
            gate_types[mode] = g["gate_type"] if g else None
    assert gate_types["manual"] == "plan_review"
    assert gate_types["plan"] == "plan_review"
    assert gate_types["auto"] == "stage_promotion"
    assert gate_types["auto"] != gate_types["plan"]
