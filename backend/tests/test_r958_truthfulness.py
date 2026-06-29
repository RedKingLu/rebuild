"""R9-5-8 tests — state truthfulness / honest markers.

Covers capability_probe (T1), output_code file-tree root (T9), profiler single-
source items + stage-artifacts existence (T2/T3/T4), precheck (T10), source/
workspace status honesty (T11/T12), and aggregate de-hardcoding.
"""

import pytest


def _make_project(client, source_type="manual", source_config=None):
    resp = client.post("/api/projects", json={
        "name": "R958", "source_type": source_type, "source_config": source_config or {},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project_id"]


# ── T1 capability_probe ────────────────────────────────────────────────────

class TestCapabilityProbe:
    def test_source_status_manual_empty(self, isolated_data):
        from app.services import capability_probe
        from app.services.workspace_service import init_workspace
        init_workspace("p-empty")
        assert capability_probe.probe_source_status("p-empty", "manual") == "empty"

    def test_source_status_git_deferred(self, isolated_data):
        from app.services import capability_probe
        from app.services.workspace_service import init_workspace
        init_workspace("p-git")
        assert capability_probe.probe_source_status("p-git", "git") == "deferred"

    def test_source_status_blocked_when_workspace_blocked(self, isolated_data):
        from app.services import capability_probe
        from app.services.workspace_service import init_workspace
        init_workspace("p-blk")
        assert capability_probe.probe_source_status("p-blk", "git", "blocked") == "blocked"

    def test_source_status_real_with_files(self, isolated_data):
        from app.services import capability_probe
        from app.services.workspace_service import init_workspace, workspace_path
        init_workspace("p-real")
        (workspace_path("p-real") / "source" / "main.py").write_text("x=1\n", encoding="utf-8")
        assert capability_probe.probe_source_status("p-real", "local_dir") == "real"

    def test_graph_status_connected(self, isolated_data):
        from app.services import capability_probe
        # graph builds in this repo → connected (real probe, not hardcoded future_r10)
        assert capability_probe.probe_graph_status() == "connected"

    def test_capability_status_not_optimistic_constant(self, isolated_data):
        from app.services import capability_probe
        val = capability_probe.probe_capability_status()
        assert val in ("available", "degraded", "not_connected")


def test_aggregate_source_status_honest_for_manual(client):
    """Workspace aggregate no longer hardcodes source_status='real'."""
    pid = _make_project(client, "manual")
    agg = client.get(f"/api/projects/{pid}/workspace").json()["data"]
    # manual project with no source → honest 'empty', NOT hardcoded 'real'
    assert agg["project"]["source_status"] == "empty"


def test_workspace_service_no_hardcoded_status_literals():
    """Grep guard: aggregate must not re-introduce the hardcoded literals (G7)."""
    from pathlib import Path
    import app.services.workspace_service as ws
    src = Path(ws.__file__).read_text(encoding="utf-8")
    assert '"source_status": "real"' not in src
    assert '"capability_status": "available"' not in src


# ── T9 output_code file-tree root ──────────────────────────────────────────

def test_output_code_filetree_root(client):
    pid = _make_project(client, "manual")
    tree = client.get(f"/api/projects/{pid}/files").json()["data"]
    roots = {r["key"] for r in tree["roots"]}
    assert "output_code" in roots


# ── T2/T3/T4 single-source identification list + stage artifacts ───────────

def test_profiling_items_single_source(client):
    from app.services.full_stack_profiler import PROFILING_ITEMS
    pid = _make_project(client, "manual")
    d = client.get(f"/api/projects/{pid}/profiling-summary").json()["data"]
    assert len(d["items"]) == len(PROFILING_ITEMS) == 12
    keys = [i["key"] for i in d["items"]]
    assert keys == [k for k, _ in PROFILING_ITEMS]
    # not yet profiled → every item exists=false (honest, not faked)
    assert all(i["exists"] is False for i in d["items"])


def test_stage_artifacts_p0_existence(client):
    pid = _make_project(client, "manual")
    d = client.get(f"/api/projects/{pid}/stage-artifacts/p0").json()["data"]
    names = {a["name"] for a in d["artifacts"]}
    assert "intake_report.json" in names
    # before onboarding the core artifacts do not exist → honest exists=false
    assert all(a["exists"] is False for a in d["artifacts"])


# ── T10 precheck ───────────────────────────────────────────────────────────

class TestPrecheck:
    def test_manual_passes(self, client):
        r = client.post("/api/projects/precheck", json={
            "name": "ok", "source_type": "manual", "source_config": {}})
        d = r.json()["data"]
        assert d["passed"] is True

    def test_empty_name_fails(self, client):
        r = client.post("/api/projects/precheck", json={
            "name": "  ", "source_type": "manual", "source_config": {}})
        # pydantic min_length=1 may 422 on whitespace? name=" " passes min_length,
        # precheck flags it. Accept either a 422 or a passed=False precheck.
        if r.status_code == 200:
            d = r.json()["data"]
            assert d["passed"] is False
            assert any(c["field"] == "name" and c["status"] == "error" for c in d["checks"])
        else:
            assert r.status_code == 422

    def test_git_without_credential_blocks(self, client):
        r = client.post("/api/projects/precheck", json={
            "name": "g", "source_type": "git",
            "source_config": {"remote_url": "https://example.com/x.git"}})
        d = r.json()["data"]
        assert d["passed"] is False
        assert any(c["field"] == "source_config" and c["status"] == "error" for c in d["checks"])

    def test_git_local_path_remote_ok(self, client):
        # local-path remote needs no credential → passes
        r = client.post("/api/projects/precheck", json={
            "name": "g", "source_type": "git",
            "source_config": {"remote_url": "/tmp/some/local/repo"}})
        d = r.json()["data"]
        assert d["passed"] is True

    def test_precheck_does_not_persist(self, client):
        before = client.get("/api/projects").json()["data"]["total"]
        client.post("/api/projects/precheck", json={
            "name": "ephemeral", "source_type": "manual", "source_config": {}})
        after = client.get("/api/projects").json()["data"]["total"]
        assert before == after  # nothing created


# ── T11/T12 source label + workspace_status honesty ────────────────────────

def test_t11_list_meta_source_status_real(client):
    _make_project(client, "manual")
    body = client.get("/api/projects").json()
    assert body["meta"]["source_status"] == "real"  # not "mock"


def test_t12_git_create_is_deferred_not_ready(client):
    pid = _make_project(client, "git",
                        {"remote_url": "https://invalid.example.com/x.git", "branch": "main"})
    proj = client.get(f"/api/projects/{pid}").json()["data"]
    # git clone of an unreachable remote → deferred or blocked, NEVER forced ready
    assert proj["workspace_status"] in ("deferred", "blocked")
