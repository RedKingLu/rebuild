"""R9-5-1 阶段C-routes tests — drive the real P0-P6 graph over HTTP (additive endpoints).

Uses `with TestClient(app)` to trigger lifespan (graph handler bootstrap + checkpointer close),
proving: real HTTP → compiled LangGraph → real P0/P1 business + DB Gates + checkpoint state,
through /graph/start, /graph/resume, /graph/state.
"""

import json

import pytest


def test_graph_http_drive_p0_p1(tmp_path, monkeypatch, isolated_data):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import workspace_service
    import app.graph.stage_handlers as sh

    # checkpoint to tmp (global settings.data_dir isn't isolated by conftest)
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path",
                        lambda: tmp_path / "graph_checkpoints.sqlite")
    # ensure lifespan re-registers real handlers onto this app instance
    sh._bootstrapped = False

    with TestClient(app) as c:
        # create a manual project
        pid = c.post("/api/projects", json={
            "name": "Graph API", "source_type": "manual", "source_config": {},
        }).json()["data"]["project_id"]

        # place a real source tree so P1 profiler has input
        src = workspace_service.workspace_path(pid) / "source"
        src.mkdir(parents=True, exist_ok=True)
        (src / "package.json").write_text(
            json.dumps({"name": "d", "dependencies": {"react": "^18"}}), encoding="utf-8")
        (src / "app.py").write_text("print(1)\n", encoding="utf-8")

        # start graph → P0 real work, pause at P0 promotion Gate
        r = c.post(f"/api/projects/{pid}/graph/start",
                   json={"execution_mode": "auto", "source_type": "manual"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        run_id = d["run_id"]
        assert d["paused"] is True
        assert d["pending_gate"]["stage"] == "p0"
        p0_gate_id = d["pending_gate"]["gate_id"]
        assert d["graph_capability_status"] == "live"
        art = workspace_service.workspace_path(pid) / "artifacts"
        assert (art / "p0" / "intake_report.json").exists(), "P0 real intake artifact via HTTP"
        # D-107: StageReports live in artifacts/{stage}/ subdirectory.
        assert (art / "p0" / "p0_acceptance.json").exists(), "P0 three-report via HTTP"

        # illegal decision → 400
        assert c.post(f"/api/projects/{pid}/graph/resume",
                      json={"run_id": run_id, "decision": "bogus"}).status_code == 400

        # resume approve → P1 real profiling, pause at P1 Gate
        # B-ACC-PROMOTION-DECISION-NOGUARD 站点②：图正暂停在某个 Gate 上时，本端点要求
        # 请求体指名 gate_id（否则 409，见 routes_graph.graph_resume）。改前这里不传
        # gate_id 也能通过，是因为守卫加固前"决策会被写到图自己暂停的那个 Gate 上"这个
        # 后果被当成了"能跑通"——这正是缺陷本身的形态，测试依赖了它。指名 p0 Gate 自己
        # 的 gate_id（图确实暂停在它上面），行为与改前完全一致，不改变本用例的验证意图。
        r = c.post(f"/api/projects/{pid}/graph/resume",
                   json={"run_id": run_id, "decision": "approve", "gate_id": p0_gate_id})
        d = r.json()["data"]
        assert d["paused"] is True
        assert d["pending_gate"]["stage"] == "p1"
        p1_gate_id = d["pending_gate"]["gate_id"]
        assert (art / "p1" / "profiling_summary.md").exists(), "P1 real profiling via HTTP"
        assert (art / "p1" / "p2_input_manifest.json").exists()

        # state endpoint reflects checkpoint
        s = c.get(f"/api/projects/{pid}/graph/state", params={"run_id": run_id}).json()["data"]
        assert s["current_stage"] == "p1"
        assert s["stage_status"].get("p0") == "completed"

        # RealGateBackend persisted gates to DB
        gates = c.get(f"/api/projects/{pid}/gates").json()["data"]["gates"]
        stages = {g["stage"] for g in gates}
        assert "p0" in stages and "p1" in stages

        # approve P1 → advance to p2. T11: p2 is now a REAL handler (RealP2Handler),
        # no longer a future_r10 stub. Force the model gateway unavailable so the P2
        # assessment blocks deterministically offline (Q-R10-2) instead of making a
        # live LLM call — then escalates to a p2 Gate (§4.10 honest, no fake completed).
        from types import SimpleNamespace
        from app.dependencies import get_services
        monkeypatch.setattr(get_services().model_gateway, "get_status",
                            lambda: SimpleNamespace(overall_status="not_configured"))
        r = c.post(f"/api/projects/{pid}/graph/resume",
                   json={"run_id": run_id, "decision": "approve", "gate_id": p1_gate_id})
        d = r.json()["data"]
        assert d["stage_status"].get("p1") == "completed"
        # p2 ran real business (blocked, not a future_r10 stub)
        assert d["stage_status"].get("p2") != "future_r10"
