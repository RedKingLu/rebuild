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
    assert d["review"]["passed"] is True      # Review Pass ran and passed
    assert "p0_artifacts" in d  # materials prepared
    # T6b / W8 full cutover (Approach A): the LangGraph P0 node is the sole P0 path
    # and Gate authority — /onboarding/complete drives the graph which creates the
    # P0→P1 Gate at complete time.
    assert d.get("gate_id"), "complete should now create the P0→P1 Gate (graph-driven)"
    assert d.get("graph_driven") is True
    active = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    assert active is not None and active["gate_status"] == "waiting_decision"
    # /onboarding/execute remains an idempotent trigger — returns the existing gate
    exec_resp = client.post(f"/api/projects/{project_id}/onboarding/execute")
    assert exec_resp.status_code == 200
    active2 = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    assert active2 is not None and active2["gate_status"] == "waiting_decision"
    assert active2["gate_id"] == active["gate_id"]  # same gate (no duplicate)


# ── Gate decision closure ─────────────────────────────────────────────────

def _make_gate(client, project_id, stage="p0"):
    client.post(f"/api/projects/{project_id}/onboarding/complete",
                json={"execution_mode": "plan"})
    # R9-3G: Gate is created by Agent-driven /onboarding/execute
    exec_resp = client.post(f"/api/projects/{project_id}/onboarding/execute")
    assert exec_resp.status_code == 200
    g = client.get(f"/api/projects/{project_id}/gates/active").json()["data"]
    return g


def test_gate_approve_advances_stage(client, project_id):
    g = _make_gate(client, project_id)
    run_id = g["run_id"]
    r = client.post(f"/api/projects/{project_id}/runs/{run_id}/stages/p0/promotion-decision",
                    json={"decision": "approve"})
    assert r.status_code == 200
    proj = client.get(f"/api/projects/{project_id}").json()["data"]
    assert proj["current_stage"] == "p1"      # advanced
    assert proj.get("active_gate") in (None, "")


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

    # Legal decision still works
    r2 = client.post(f"/api/projects/{project_id}/runs/{run_id}/stages/p0/promotion-decision",
                     json={"decision": "approve"})
    assert r2.status_code == 200
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
