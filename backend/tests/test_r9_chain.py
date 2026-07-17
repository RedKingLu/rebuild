"""R9-3F chain tests — close the coverage gap that let runtime-fatal bugs ship.

Covers the P0→P1 minimal real chain end to end through the real API:
  - profiler module imports + /profile generates artifacts + P2 manifest
  - /onboarding/complete creates Run + intake_report + P0→P1 Gate (waiting_decision)
  - Gate decision closure: approve advances stage, reject blocks,
    request_changes -> changes_requested, illegal -> HTTP 400
  - Review Pass framework: pass / fail-retry / escalate
  - three-mode authorization behavior + Auto proxy fields
  - source materialization honesty (manual->empty, git->deferred, local_dir->completed)
"""

import os
import time
import pytest


@pytest.fixture
def project_id(client):
    resp = client.post("/api/projects", json={
        "name": "R9-3F Chain Test", "source_type": "manual", "source_config": {},
    })
    assert resp.status_code == 200
    return resp.json()["data"]["project_id"]


# ── profiler import (regression guard for the SyntaxError) ────────────────

def test_profiler_module_imports():
    import importlib
    import app.services.full_stack_profiler as m
    importlib.reload(m)
    assert hasattr(m, "FullStackProfiler")


# ── P0 onboarding chain ───────────────────────────────────────────────────

def test_onboarding_creates_run_intake_and_gate(client, project_id):
    resp = client.post(f"/api/projects/{project_id}/onboarding/complete",
                       json={"execution_mode": "auto", "env_kind": "local",
                             "language_hint": "Python", "framework_hint": "FastAPI"})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["onboarding_done"] is True
    assert d["run_id"]
    assert d["intake_artifact_id"]
    assert "p0_artifacts" in d  # materials prepared
    # R17-X 联调修复（用户裁决，回退 T6b/W8 "complete 自动驱动"）: /onboarding/complete
    # 不再自动驱动 P0 图，也不再创建 P0→P1 Gate，也不假装已审核。P0 图执行完全交给用户
    # 点「开始」触发的 POST /onboarding/execute。
    assert d["review"]["passed"] is None, "complete 不再审核，verdict 未知(None)"
    assert d.get("gate_id") is None, "complete 不应创建 Gate（不自动执行）"
    assert d.get("graph_driven") is False, "complete 不驱动图"
    # 回归锁: complete 后 project 无 active P0 gate（证明不自动执行 P0）
    active_after_complete = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    assert active_after_complete is None, \
        f"complete 后不得有 active gate（P0 尚未启动），got {active_after_complete}"

    # 用户点「开始」→ POST /onboarding/execute 才驱动图并创建 P0→P1 Gate
    exec_resp = client.post(f"/api/projects/{project_id}/onboarding/execute")
    assert exec_resp.status_code == 200
    active = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    assert active is not None and active["gate_status"] == "waiting_decision"
    # /onboarding/execute is idempotent — a second call returns the same gate (no duplicate)
    exec_resp2 = client.post(f"/api/projects/{project_id}/onboarding/execute")
    assert exec_resp2.status_code == 200
    active2 = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    assert active2 is not None and active2["gate_status"] == "waiting_decision"
    assert active2["gate_id"] == active["gate_id"]  # same gate (no duplicate)


# ── Gate decision closure ─────────────────────────────────────────────────

def _ensure_task_graph(run_id: str, stage: str):
    """R17-2 V-R17-1B-2: gate 晋级强绑阶段产物。给测试 run 注入 task_graph 以满足校验。"""
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph
    import uuid
    db = get_session()
    try:
        tg = db.query(TaskGraph).filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage).first()
        if tg is None:
            tg = TaskGraph(task_graph_id=f"tg-test-{uuid.uuid4().hex[:8]}", project_id="",
                           run_id=run_id, stage=stage, title=f"test gate {stage}", graph_status="completed")
            db.add(tg)
            db.commit()
    finally:
        db.close()


def _make_gate(client, project_id, stage="p0", mode="auto"):
    """创建 gate。R17-X: 默认 auto 模式跳过 plan gate（直接 stage_promotion）；
    mode="plan"/"manual" 走两阶段流程（plan_presentation → stage_promotion，欢迎门已删除）。"""
    client.post(f"/api/projects/{project_id}/onboarding/complete",
                json={"execution_mode": mode})
    exec_resp = client.post(f"/api/projects/{project_id}/onboarding/execute")
    assert exec_resp.status_code == 200, exec_resp.text
    g = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    # R17-2: 注入 task_graph 以满足 gate 晋级强绑阶段产物校验
    _ensure_task_graph(g.get("run_id", ""), g.get("stage", "p0"))
    return g


def _wait_for_stage(client, project_id, expected, timeout=None):
    """Poll until the project reaches `expected` stage (graph runs in background thread)."""
    if timeout is None:
        timeout = int(os.environ.get("R176_GRAPH_WAIT_TIMEOUT", "120"))
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/projects/{project_id}").json()["data"]["current_stage"]
        if last == expected:
            return True
        time.sleep(0.5)
    raise AssertionError(f"stage never reached {expected} in {timeout}s, last={last}")


def test_gate_approve_advances_stage(client, project_id):
    g = _make_gate(client, project_id)
    run_id = g["run_id"]
    r = client.post(f"/api/projects/{project_id}/runs/{run_id}/stages/p0/promotion-decision",
                    json={"decision": "approve"})
    assert r.status_code == 200
    assert r.json()["data"]["transition_mode"] == "real_background"  # async graph
    _wait_for_stage(client, project_id, "p1")      # advanced in background thread
    proj = client.get(f"/api/projects/{project_id}").json()["data"]
    assert proj["current_stage"] == "p1"
    # WP-1 NEW-02: after advancing, the p1 work node produces a fresh pending Gate and
    # project.active_gate must point at THAT real waiting gate (previously _run_graph_bg
    # hardcoded active_gate="" here, so the frontend lost the active Gate after every
    # promotion). Assert the corrected behavior: active_gate is a real waiting p1 gate.
    active_gate_id = proj.get("active_gate")
    assert active_gate_id, "active_gate must point at the new p1 waiting Gate (NEW-02)"
    ag = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    assert ag is not None and ag["gate_id"] == active_gate_id
    assert ag["stage"] == "p1"
    assert ag["gate_status"] == "waiting_decision"


def test_gate_request_changes_status(client, project_id):
    g = _make_gate(client, project_id)
    gid = g["gate_id"]
    r = client.post(f"/api/projects/{project_id}/gates/{gid}/decision",
                    json={"decision": "request_changes", "reason": "需补充"})
    assert r.status_code == 200
    assert r.json()["data"]["gate"]["gate_status"] == "changes_requested"


def test_gate_reject_status(client, project_id):
    g = _make_gate(client, project_id)
    gid = g["gate_id"]
    r = client.post(f"/api/projects/{project_id}/gates/{gid}/decision",
                    json={"decision": "reject"})
    assert r.status_code == 200
    assert r.json()["data"]["gate"]["gate_status"] == "rejected"


def test_gate_illegal_decision_rejected(client, project_id):
    g = _make_gate(client, project_id)
    gid = g["gate_id"]
    r = client.post(f"/api/projects/{project_id}/gates/{gid}/decision",
                    json={"decision": "frobnicate"})
    assert r.status_code == 400                # illegal decision -> 400
    # state unchanged (still awaiting)
    g2 = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    assert g2 is not None and g2["gate_status"] == "waiting_decision"


def test_promotion_decision_illegal_value_rejected(client, project_id):
    """R10-5 P1-B: /promotion-decision must reject illegal decision with 400,
    must NOT advance the stage, and must NOT create a duplicate stage_promotion Gate."""
    g = _make_gate(client, project_id)
    run_id = g["run_id"]
    gates_before = client.get(f"/api/projects/{project_id}/gates").json()["data"]["gates"]
    n_before = len(gates_before)

    r = client.post(f"/api/projects/{project_id}/runs/{run_id}/stages/p0/promotion-decision",
                    json={"decision": "badvalue"})
    assert r.status_code == 400                # illegal -> 400 (was 200 before fix)

    # No duplicate Gate created, stage not advanced
    gates_after = client.get(f"/api/projects/{project_id}/gates").json()["data"]["gates"]
    assert len(gates_after) == n_before
    proj = client.get(f"/api/projects/{project_id}").json()["data"]
    assert proj["current_stage"] == "p0"       # not advanced

    # Legal decision still works (graph runs in background)
    r2 = client.post(f"/api/projects/{project_id}/runs/{run_id}/stages/p0/promotion-decision",
                     json={"decision": "approve"})
    assert r2.status_code == 200
    assert r2.json()["data"]["transition_mode"] == "real_background"
    _wait_for_stage(client, project_id, "p1")
    proj2 = client.get(f"/api/projects/{project_id}").json()["data"]
    assert proj2["current_stage"] == "p1"


# ── P1 profiling chain ────────────────────────────────────────────────────

def test_profile_generates_artifacts_and_p2_manifest(client, tmp_path):
    # local_dir project pointing at a small real source tree
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("import flask\nprint('hi')\n", encoding="utf-8")
    (src / "requirements.txt").write_text("flask==2.0\n", encoding="utf-8")
    (src / "README.md").write_text("# demo\n", encoding="utf-8")
    pid = client.post("/api/projects", json={
        "name": "P1 prof", "source_type": "local_dir",
        "source_config": {"path": str(src)}}).json()["data"]["project_id"]
    client.post(f"/api/projects/{pid}/materialize", json={})
    r = client.post(f"/api/projects/{pid}/profile", json={})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["review"]["passed"] is True
    assert d["profiling_result"]["items_completed"] >= 1
    assert d["gate_id"]
    # profiling summary + artifacts visible
    s = client.get(f"/api/projects/{pid}/profiling-summary").json()["data"]
    assert s["available"] is True
    assert "p2_input_manifest.json" in s["artifacts"]


# ── Review Pass framework (pass / fail-retry / escalate) ──────────────────

def test_review_pass_escalates_after_max_rounds():
    import asyncio
    from app.services.review_pass import ReviewPass, ReviewResult

    calls = {"n": 0}

    def execute():
        calls["n"] += 1
        return {"round": calls["n"]}

    def review(_):
        return ReviewResult(passed=False, issues=[{"type": "x"}])

    rp = ReviewPass(max_rounds=2)
    out = asyncio.run(
        rp.run(execute_fn=execute, review_fn=review, project_id="p", stage="p1"))
    assert out["passed"] is False
    assert out["escalated_to_gate"] is True
    assert calls["n"] == 2          # retried up to max_rounds


def test_review_pass_passes_first_round():
    import asyncio
    from app.services.review_pass import ReviewPass, ReviewResult
    rp = ReviewPass(max_rounds=2)
    out = asyncio.run(
        rp.run(execute_fn=lambda: {"ok": 1},
               review_fn=lambda r: ReviewResult(passed=True), project_id="p", stage="p0"))
    assert out["passed"] is True
    assert out["escalated_to_gate"] is False


# ── Three-mode authorization behavior + Auto proxy ────────────────────────

def test_mode_manual_requires_confirmation(client, project_id):
    client.put(f"/api/projects/{project_id}/mode", json={"mode": "manual"})
    r = client.post(f"/api/projects/{project_id}/execute",
                    json={"command": "print(1)", "language": "python"})
    d = r.json()["data"]
    assert d.get("requires_confirmation") is True
    assert d["authorization"]["decision"] == "require_confirmation"
    # With explicit confirm -> executes
    r2 = client.post(f"/api/projects/{project_id}/execute",
                     json={"command": "print(1)", "language": "python", "confirm": True})
    assert r2.json()["data"].get("requires_confirmation") is not True


def test_mode_auto_authorizes_and_executes(client, project_id):
    client.put(f"/api/projects/{project_id}/mode", json={"mode": "auto"})
    r = client.post(f"/api/projects/{project_id}/execute",
                    json={"command": "print(42)", "language": "python", "timeout": 20})
    d = r.json()["data"]
    assert d["authorization"]["decision"] == "auto_approved"
    assert d["authorization"]["reviewer"] == "auto_authorizer"
    assert d["exit_code"] == 0 and "42" in d["stdout"]


def test_mode_auto_proxy_fields_present():
    from app.services.mode_policy import authorize_action
    a = authorize_action("auto", "L2", "x")
    for k in ("decision", "reason", "risk_level", "mode", "reviewer",
              "evidence_refs", "trace_refs", "audit_ref"):
        assert k in a
    # Auto must NOT auto-approve L4+
    high = authorize_action("auto", "L4", "danger")
    assert high["decision"] == "require_confirmation"


def test_mode_persists_and_reads_back(client, project_id):
    client.put(f"/api/projects/{project_id}/mode", json={"mode": "manual"})
    got = client.get(f"/api/projects/{project_id}/mode").json()["data"]
    assert got["execution_mode"] == "manual"


# ── Source materialization honesty ────────────────────────────────────────

def test_materialize_manual_is_empty_not_completed(client, project_id):
    r = client.post(f"/api/projects/{project_id}/materialize", json={})
    assert r.json()["data"]["materialization_status"] == "empty"


def test_materialize_git_is_deferred_not_completed(client):
    pid = client.post("/api/projects", json={
        "name": "git", "source_type": "git",
        "source_config": {"remote_url": "https://example.com/x.git", "branch": "main"}}
    ).json()["data"]["project_id"]
    r = client.post(f"/api/projects/{pid}/materialize", json={})
    d = r.json()["data"]
    assert d["materialization_status"] == "deferred"
    assert d["success"] is False


def test_materialize_local_dir_blocks_sensitive_root(client):
    pid = client.post("/api/projects", json={
        "name": "sens", "source_type": "local_dir",
        "source_config": {"path": "/etc"}}).json()["data"]["project_id"]
    r = client.post(f"/api/projects/{pid}/materialize", json={})
    d = r.json()["data"]
    assert d["materialization_status"] == "failed"
    assert any(g["type"] == "local_dir_sensitive_blocked" for g in d["evidence_gaps"])


# ── Git clone materialization (P0-1 fix: real git clone) ────────────────

def test_git_materialize_clones_real_repo(client, tmp_path):
    """Create a real local git repo and verify materializer clones it."""
    import subprocess
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Test Repo\nHello.\n", encoding="utf-8")
    (repo_dir / "main.py").write_text("print(42)\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_dir), capture_output=True, check=True)

    pid = client.post("/api/projects", json={
        "name": "git-clone-test", "source_type": "git",
        "source_config": {"remote_url": str(repo_dir), "branch": "main"},
    }).json()["data"]["project_id"]
    r = client.post(f"/api/projects/{pid}/materialize", json={})
    assert r.status_code == 200
    d = r.json()["data"]
    # git clone of a local path should succeed
    assert d["materialization_status"] == "completed", f"expected completed, got {d['materialization_status']}: {d.get('errors')} {d.get('evidence_gaps')}"
    assert d["file_count"] >= 1
    assert d["success"] is True


def test_git_materialize_invalid_url_is_deferred(client):
    """Invalid git URL should be honestly deferred, not crash."""
    pid = client.post("/api/projects", json={
        "name": "bad-git", "source_type": "git",
        "source_config": {"remote_url": "https://invalid.example.com/nonexistent.git", "branch": "main"},
    }).json()["data"]["project_id"]
    r = client.post(f"/api/projects/{pid}/materialize", json={})
    assert r.status_code == 200
    d = r.json()["data"]
    # Should fail to clone → deferred
    assert d["materialization_status"] == "deferred"
    assert d["success"] is False
    assert any("git_clone_failed" in str(g) for g in d.get("evidence_gaps", []))


# ── coding-agents list (regression for generator/session 500) ─────────────

def test_coding_agents_list_ok(client):
    r = client.get("/api/coding-agents")
    assert r.status_code == 200
    assert "agents" in r.json()["data"]
