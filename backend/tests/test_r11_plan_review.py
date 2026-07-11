"""R17-X B-R17X-PLANREVIEW-1 regression: the per-stage plan_review「欢迎门」is REMOVED.

Manual/Plan modes now go straight to a plan_presentation Gate (审计划, D-025) BEFORE
executing stage actions; Auto self-reviews and executes straight to a stage_promotion
Gate (D-023). The "欢迎 + 启动" step is a one-time frontend initial page, not a per-stage
graph gate anymore.

New flow (manual/plan):
    p0_work (plan_only → 生成 start_plan) → plan_presentation Gate → approve
        → p0_work (full StageLoop → 执行 + acceptance) → stage_promotion Gate → approve → p1

Auto flow:
    p0_work (full StageLoop 立即执行) → stage_promotion Gate → approve → p1

Uses `with TestClient(app)` + tmp checkpoint (test_graph_api / test_w9w10 pattern) so
the graph checkpoint thread survives across requests for the plan-approval resume.
No mock, no fabricated pass — asserts on the live gate type + real artifacts on disk.
"""

import time
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


def _wait_for_gate_type(c, pid, gate_type, timeout=None):
    """Poll for the active gate to become `gate_type` (graph resume runs in a bg thread)."""
    import os
    if timeout is None:
        timeout = int(os.environ.get("R176_GRAPH_WAIT_TIMEOUT", "120"))
    deadline = time.time() + timeout
    gate = _active_gate(c, pid)
    while (gate is None or gate["gate_type"] != gate_type) and time.time() < deadline:
        time.sleep(0.5)
        gate = _active_gate(c, pid)
    return gate


def test_plan_mode_pauses_at_plan_presentation_before_execution(tmp_path, monkeypatch, isolated_data):
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = _make_project(c, "plan-mode")
        c.post(f"/api/projects/{pid}/onboarding/complete",
               json={"execution_mode": "plan"})

        # R17-X 联调修复: /onboarding/complete 不再自动驱动 P0 图、不建 Gate。先断言 complete
        # 后无 active gate（证明不自动执行），再由用户点「开始」触发的 execute 驱动图。
        assert _active_gate(c, pid) is None, "complete 后不应有 active gate（P0 尚未启动）"
        ex = c.post(f"/api/projects/{pid}/onboarding/execute")
        assert ex.status_code == 200, ex.text

        # 1) first gate is a plan_presentation gate directly — NO welcome plan_review gate,
        #    and NOT a promotion gate (B-R17X-PLANREVIEW-1: 欢迎门已删除)
        gate = _active_gate(c, pid)
        assert gate is not None, "plan mode must produce a gate to review"
        assert gate["gate_type"] == "plan_presentation", \
            f"expected plan_presentation (welcome门 removed), got {gate['gate_type']}"

        # 2) the plan (起始计划) is available for review, but execution has NOT happened yet
        art = _artifacts(pid)
        assert (art / "p0_start_plan.json").exists(), "plan (起始计划) must be produced for review"
        assert not (art / "p0_acceptance.json").exists(), \
            "stage actions must NOT run before the plan is approved (审核通过后才执行动作)"

        # 3) 批准 plan_presentation → agent 执行完整 StageLoop → stage_promotion gate（含完整结果）
        gid = gate["gate_id"]
        r = c.post(f"/api/projects/{pid}/gates/{gid}/decision", json={"decision": "approve"})
        assert r.status_code == 200, r.text

        gate2 = _wait_for_gate_type(c, pid, "stage_promotion")
        assert gate2 is not None and gate2["gate_type"] == "stage_promotion", \
            f"after plan_presentation approve, stage_promotion gate should appear, got {gate2}"
        assert (art / "p0_acceptance.json").exists(), \
            "stage actions (and the real acceptance report) must run after plan approval"


def test_manual_mode_also_reviews_plan_first(tmp_path, monkeypatch, isolated_data):
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = _make_project(c, "manual-mode")
        c.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "manual"})
        # R17-X: complete 不驱动图；execute 才驱动
        assert _active_gate(c, pid) is None, "complete 后不应有 active gate"
        ex = c.post(f"/api/projects/{pid}/onboarding/execute")
        assert ex.status_code == 200, ex.text
        gate = _active_gate(c, pid)
        assert gate is not None and gate["gate_type"] == "plan_presentation", \
            f"manual mode must review the plan first (plan_presentation), got {gate}"
        assert not (_artifacts(pid) / "p0_acceptance.json").exists(), \
            "manual mode must NOT execute stage actions before plan approval"


def test_auto_mode_skips_plan_review_and_executes(tmp_path, monkeypatch, isolated_data):
    with _client_with_graph(tmp_path, monkeypatch) as c:
        pid = _make_project(c, "auto-mode")
        c.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
        # R17-X: complete 不驱动图；execute 才驱动
        assert _active_gate(c, pid) is None, "complete 后不应有 active gate"
        ex = c.post(f"/api/projects/{pid}/onboarding/execute")
        assert ex.status_code == 200, ex.text
        gate = _active_gate(c, pid)
        # Auto self-reviews the plan → no user plan gate → straight to the promotion gate
        assert gate is not None and gate["gate_type"] == "stage_promotion", \
            f"auto mode must NOT insert a plan gate, got {gate}"
        assert (_artifacts(pid) / "p0_acceptance.json").exists(), \
            "auto mode executes stage actions immediately"


def test_no_plan_review_welcome_gate_is_ever_created(tmp_path, monkeypatch, isolated_data):
    """B-R17X-PLANREVIEW-1: the per-stage plan_review 欢迎门 must be gone entirely —
    no mode may surface a plan_review gate. R17-X: complete 不自动执行，execute 才驱动图。"""
    with _client_with_graph(tmp_path, monkeypatch) as c:
        for mode in ("manual", "plan", "auto"):
            pid = _make_project(c, f"no-welcome-{mode}")
            c.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": mode})
            # R17-X 回归锁: complete 后无 active gate（证明不自动执行 P0）
            assert _active_gate(c, pid) is None, \
                f"{mode} mode: complete 不应创建 gate（不自动执行）"
            ex = c.post(f"/api/projects/{pid}/onboarding/execute")
            assert ex.status_code == 200, ex.text
            g = _active_gate(c, pid)
            assert g is not None, f"{mode} mode must produce a gate after execute"
            assert g["gate_type"] != "plan_review", \
                f"{mode} mode must NOT create a plan_review welcome gate, got {g['gate_type']}"


def test_three_modes_are_behaviorally_distinct(tmp_path, monkeypatch, isolated_data):
    """One assertion set proving Manual/Plan differ from Auto at the P0 entry."""
    with _client_with_graph(tmp_path, monkeypatch) as c:
        gate_types = {}
        for mode in ("manual", "plan", "auto"):
            pid = _make_project(c, f"distinct-{mode}")
            c.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": mode})
            # R17-X: execute 驱动图（complete 不自动执行）
            ex = c.post(f"/api/projects/{pid}/onboarding/execute")
            assert ex.status_code == 200, ex.text
            g = _active_gate(c, pid)
            gate_types[mode] = g["gate_type"] if g else None
    assert gate_types["manual"] == "plan_presentation"
    assert gate_types["plan"] == "plan_presentation"
    assert gate_types["auto"] == "stage_promotion"
    assert gate_types["auto"] != gate_types["plan"]
