"""R9-5-7b tests — W9/W10 legacy routes driving the LangGraph checkpoint thread.

Proves the graph-driven branch of the legacy decision/run-lifecycle routes is LIVE
(not dead code): when a run was started on the graph (paused at an interrupt), the
legacy promotion-decision and run-resume endpoints drive FlowRuntime.resume on the
same thread (thread_id=run_id), sync the project/run DB from the graph state, and
record the decision through the single GateService.decide kernel (no double-advance).

Uses `with TestClient(app)` to trigger lifespan (real graph handler bootstrap), per
the test_graph_api.py pattern. Non-graph behavior equivalence is covered by
test_r9_chain.py (19 cases) which remains green.
"""

import json

import pytest


@pytest.fixture(autouse=True)
def _reset_graph_singletons():
    """Reset the FlowRuntime + checkpointer singletons so each `with TestClient`
    lifespan rebuilds a fresh compiled graph bound to the current checkpointer
    (otherwise a stale graph references a closed aiosqlite connection)."""
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
    # checkpoint to tmp (global settings.data_dir isn't isolated by conftest)
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path",
                        lambda: tmp_path / "graph_checkpoints.sqlite")
    sh._bootstrapped = False  # force lifespan to re-register real handlers
    return TestClient(app)


def test_w9_promotion_decision_drives_graph(tmp_path, monkeypatch, isolated_data):
    """W9: legacy promotion-decision endpoint resumes the graph thread + syncs DB."""
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = c.post("/api/projects", json={
            "name": "W9", "source_type": "manual", "source_config": {},
        }).json()["data"]["project_id"]

        # start the graph → pauses at the P0 promotion Gate (real thread exists)
        d = c.post(f"/api/projects/{pid}/graph/start",
                   json={"execution_mode": "auto", "source_type": "manual"}).json()["data"]
        run_id = d["run_id"]
        assert d["paused"] is True and d["pending_gate"]["stage"] == "p0"

        # drive the LEGACY promotion-decision endpoint (NOT /graph/resume)
        r = c.post(f"/api/projects/{pid}/runs/{run_id}/stages/p0/promotion-decision",
                   json={"decision": "approve"})
        assert r.status_code == 200, r.text
        result = r.json()["data"]
        assert result["graph_driven"] is True            # resumed via FlowRuntime

        # graph advanced to p1 AND project DB was synced from graph state
        s = c.get(f"/api/projects/{pid}/graph/state", params={"run_id": run_id}).json()["data"]
        assert s["current_stage"] == "p1"
        proj = c.get(f"/api/projects/{pid}").json()["data"]
        assert proj["current_stage"] == "p1"             # DB synced, no divergence

        # the P0 gate decision was recorded (single kernel) as approved
        gates = c.get(f"/api/projects/{pid}/gates").json()["data"]["gates"]
        p0_gate = [g for g in gates if g["stage"] == "p0" and g["gate_type"] == "stage_promotion"][0]
        assert p0_gate["gate_status"] == "approved"


def test_w10_resume_run_drives_graph(tmp_path, monkeypatch, isolated_data):
    """W10: legacy run-resume endpoint drives the graph thread when one is paused."""
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = c.post("/api/projects", json={
            "name": "W10", "source_type": "manual", "source_config": {},
        }).json()["data"]["project_id"]
        d = c.post(f"/api/projects/{pid}/graph/start",
                   json={"execution_mode": "auto", "source_type": "manual"}).json()["data"]
        run_id = d["run_id"]
        assert d["paused"] is True

        # legacy run resume with a decision → drives the graph (no "(mock)" text)
        r = c.post(f"/api/projects/{pid}/runs/{run_id}/resume", json={"decision": "approve"})
        assert r.status_code == 200, r.text

        # graph advanced past p0 (now paused at p1 gate)
        s = c.get(f"/api/projects/{pid}/graph/state", params={"run_id": run_id}).json()["data"]
        assert s["stage_status"].get("p0") == "completed"


def test_w10_no_mock_trace_text(tmp_path, monkeypatch, isolated_data):
    """W10: run-lifecycle routes no longer emit '(mock)' Trace summaries."""
    import app.api.routes_runs as rr
    from pathlib import Path
    src = Path(rr.__file__).read_text(encoding="utf-8")
    assert "(mock)" not in src and "mock transition" not in src
