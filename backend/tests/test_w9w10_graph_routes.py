"""R9-5-7b tests — W9/W10 legacy routes driving the LangGraph checkpoint thread.

Proves the graph-driven branch of the legacy `promotion-decision` route is LIVE (not
dead code): when a run was started on the graph (paused at an interrupt) AND the
decision names the Gate the graph is actually paused on, the endpoint drives
FlowRuntime.resume on the same thread (thread_id=run_id), syncs the project/run DB
from the graph state, and records the decision through the single GateService.decide
kernel (no double-advance).

`run-resume`（W10）的图驱动分支已被 B-ACC-PROMOTION-DECISION-NOGUARD 站点②收紧：该端点
连 gate_id 字段都没有，无法安全指名被决策的对象，故图正暂停在某个 Gate 上时一律 409、
不再驱动图（见 test_w10_resume_run_refuses_decision_while_paused_on_gate）。这不是本文件
断言的削弱，而是同一批次修复的一部分：不安全的"驱动"能力被移除，安全路径
（`/gates/{gate_id}/decision`、`/stages/{stage}/promotion-decision`）继续覆盖同等场景。

Uses `with TestClient(app)` to trigger lifespan (real graph handler bootstrap), per
the test_graph_api.py pattern. Non-graph behavior equivalence is covered by
test_r9_chain.py (19 cases) which remains green.
"""

import json
import os
import time

import pytest


def _wait_for_project_stage(c, pid, expected, timeout=None):
    """Poll the project DB until it reaches `expected` stage. R17-6: promotion-decision
    drives the graph fire-and-forget in a background thread (transition_mode=
    real_background), so the p0→p1 advance + DB sync land shortly AFTER the 200
    response — mirror test_r9_chain._wait_for_stage rather than reading immediately."""
    if timeout is None:
        timeout = int(os.environ.get("R176_GRAPH_WAIT_TIMEOUT", "120"))
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = c.get(f"/api/projects/{pid}").json()["data"]["current_stage"]
        if last == expected:
            return
        time.sleep(0.5)
    raise AssertionError(f"stage never reached {expected} in {timeout}s, last={last}")


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

        # R17-6: graph advance runs in the background thread → wait for the DB sync,
        # then assert graph/state + project DB both reflect p1 (no divergence).
        _wait_for_project_stage(c, pid, "p1")
        s = c.get(f"/api/projects/{pid}/graph/state", params={"run_id": run_id}).json()["data"]
        assert s["current_stage"] == "p1"
        proj = c.get(f"/api/projects/{pid}").json()["data"]
        assert proj["current_stage"] == "p1"             # DB synced, no divergence

        # the P0 gate decision was recorded (single kernel) as approved
        gates = c.get(f"/api/projects/{pid}/gates").json()["data"]["gates"]
        p0_gate = [g for g in gates if g["stage"] == "p0" and g["gate_type"] == "stage_promotion"][0]
        assert p0_gate["gate_status"] == "approved"


def test_w10_resume_run_refuses_decision_while_paused_on_gate(tmp_path, monkeypatch, isolated_data):
    """W10（**行为已被 B-ACC-PROMOTION-DECISION-NOGUARD 站点②收紧，本用例随之更新**）：
    legacy run-resume 端点**不再**能替 Gate 做决策。

    本用例原断言"图暂停时提交 decision 会驱动图并晋级"（`r.status_code == 200`），这正是
    缺陷本身的形态：该端点连 gate_id 字段都没有，决策会被写到【图自己暂停的那个】Gate
    上，与调用方是否真的想决策"那一个" Gate 无关。真跑已坐实同族缺陷（经另一个 Gate 的
    决策提交，凭空触发一次未授权的阶段晋级）。用户 2026-09-15 裁决 Q-A「失效吧」——
    这一用法应当失效，故改测试断言新行为（409 + 不驱动图），不放宽守卫。
    生产路径请用 `/gates/{gate_id}/decision` 或 `/stages/{stage}/promotion-decision`
    （两者都已加同一性判据，能安全指名被决策的 Gate）。"""
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = c.post("/api/projects", json={
            "name": "W10", "source_type": "manual", "source_config": {},
        }).json()["data"]["project_id"]
        d = c.post(f"/api/projects/{pid}/graph/start",
                   json={"execution_mode": "auto", "source_type": "manual"}).json()["data"]
        run_id = d["run_id"]
        assert d["paused"] is True

        # 图正暂停在 p0 promotion Gate 上 → run-resume 不得替它做决策
        r = c.post(f"/api/projects/{pid}/runs/{run_id}/resume", json={"decision": "approve"})
        assert r.status_code == 409, r.text

        # 图未被驱动：仍停在 p0，未晋级
        s = c.get(f"/api/projects/{pid}/graph/state", params={"run_id": run_id}).json()["data"]
        assert s["stage_status"].get("p0") != "completed"
        assert s["current_stage"] == "p0"


def test_w10_no_mock_trace_text(tmp_path, monkeypatch, isolated_data):
    """W10: run-lifecycle routes no longer emit '(mock)' Trace summaries."""
    import app.api.routes_runs as rr
    from pathlib import Path
    src = Path(rr.__file__).read_text(encoding="utf-8")
    assert "(mock)" not in src and "mock transition" not in src
