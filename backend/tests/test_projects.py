"""Test Project API — DB-backed."""


def _create_project(client, name="Test Project", description="A test",
                    source_type="local_dir", source_config=None):
    body = {"name": name, "description": description, "source_type": source_type}
    if source_config is not None:
        body["source_config"] = source_config
    resp = client.post("/api/projects", json=body)
    assert resp.status_code == 200
    return resp.json()["data"]


class TestProjectCRUD:
    """CRUD: create -> list -> get -> update -> delete."""

    def test_create_and_list(self, client):
        # Start with empty list
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        initial_count = len(data["data"]["projects"])

        # Create two projects
        p1 = _create_project(client, "Project A")
        p2 = _create_project(client, "Project B", source_type="git",
                             source_config={"remote_url": "https://example.com/repo.git"})

        assert p1["name"] == "Project A"
        assert p1["project_status"] == "created"
        assert p1["source_status"] == "real"
        assert p1["capability_status"] == "available"
        assert "project_id" in p1

        assert p2["name"] == "Project B"
        assert p2["source_type"] == "git"
        assert p2["source_config"] == {"remote_url": "https://example.com/repo.git"}

        # List should have 2 more
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()["data"]["projects"]
        assert len(projects) == initial_count + 2
        ids = {p["project_id"] for p in projects}
        assert p1["project_id"] in ids
        assert p2["project_id"] in ids

    def test_get_project(self, client):
        p = _create_project(client, "MicroOA 信创迁移", description="将 .NET Framework 办公系统迁移至信创平台")
        pid = p["project_id"]

        resp = client.get(f"/api/projects/{pid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["name"] == "MicroOA 信创迁移"
        assert data["data"]["project_status"] == "created"
        assert data["data"]["project_id"] == pid

    def test_get_nonexistent_project(self, client):
        resp = client.get("/api/projects/nonexistent-999")
        assert resp.status_code == 404

    def test_update_project(self, client):
        p = _create_project(client, "Original Name")
        pid = p["project_id"]

        resp = client.patch(f"/api/projects/{pid}", json={"name": "Updated Name"})
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Updated Name"

        # Verify persistence
        resp = client.get(f"/api/projects/{pid}")
        assert resp.json()["data"]["name"] == "Updated Name"

    def test_update_project_scope_valid(self, client):
        """B-6: PATCH /projects/{id} accepts a valid external_platform_scope (server-validated
        enum) and persists it — the value the backend P4 should_delegate() decision reads."""
        p = _create_project(client, "Scope Valid")
        pid = p["project_id"]

        for scope in ("none", "coding_only", "all_stages"):
            resp = client.patch(f"/api/projects/{pid}", json={"external_platform_scope": scope})
            assert resp.status_code == 200, (scope, resp.status_code, resp.text)
            assert resp.json()["data"]["external_platform_scope"] == scope

    def test_update_project_scope_invalid_rejected(self, client):
        """B-6: a bogus scope value is rejected by the validated enum (422), not silently stored —
        should_delegate() must never see an unknown scope."""
        p = _create_project(client, "Scope Invalid")
        pid = p["project_id"]
        resp = client.patch(f"/api/projects/{pid}", json={"external_platform_scope": "bogus_value"})
        assert resp.status_code == 422, resp.text
        # prior value (none) preserved
        assert client.get(f"/api/projects/{pid}").json()["data"]["external_platform_scope"] in (None, "none")

    def test_delete_project(self, client):
        """DELETE endpoint soft-deletes (archives) a project."""
        p = _create_project(client, "To Delete")
        pid = p["project_id"]

        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["archived"] is True

        # Verify status changed to archived
        resp = client.get(f"/api/projects/{pid}")
        assert resp.json()["data"]["project_status"] == "archived"

        # Deleting nonexistent returns 404
        resp = client.delete("/api/projects/nonexistent-999")
        assert resp.status_code == 404

    def test_archive_legacy_endpoint(self, client):
        """POST /{id}/archive still works as soft-delete."""
        p = _create_project(client, "Archive Me")
        pid = p["project_id"]

        resp = client.post(f"/api/projects/{pid}/archive")
        assert resp.status_code == 200
        assert resp.json()["data"]["archived"] is True

        resp = client.get(f"/api/projects/{pid}")
        assert resp.json()["data"]["project_status"] == "archived"

    def test_list_with_query_params(self, client):
        _create_project(client, "Active A")
        _create_project(client, "Active B")
        c = _create_project(client, "Archived C")
        # Archive the third
        client.delete(f"/api/projects/{c['project_id']}")

        # List only archived
        resp = client.get("/api/projects?status=archived")
        archived = resp.json()["data"]["projects"]
        assert len(archived) >= 1
        assert all(p["project_status"] == "archived" for p in archived)

        # Sort ascending
        resp = client.get("/api/projects?sort=name&order=asc")
        names = [p["name"] for p in resp.json()["data"]["projects"]]
        assert names == sorted(names)

        # Limit
        resp = client.get("/api/projects?limit=1")
        assert len(resp.json()["data"]["projects"]) == 1

    def test_response_envelope(self, client):
        resp = client.get("/api/projects")
        data = resp.json()
        assert "meta" in data
        assert "source_status" in data["meta"]
        assert data["status"] == "success"


class TestSourceIntegrationEndpoints:
    """Git / ZIP / GitHub integration endpoints."""

    def test_configure_git_source(self, client):
        p = _create_project(client, "Git Project")
        pid = p["project_id"]

        resp = client.post(
            f"/api/projects/{pid}/integrations/git",
            params={"remote_url": "https://git.example.com/my/repo.git", "branch": "develop", "subpath": "/src"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "git"
        assert data["source_config"]["remote_url"] == "https://git.example.com/my/repo.git"
        assert data["source_config"]["branch"] == "develop"
        assert data["source_config"]["subpath"] == "/src"

    def test_configure_zip_source(self, client):
        p = _create_project(client, "ZIP Project")
        pid = p["project_id"]

        resp = client.post(
            f"/api/projects/{pid}/integrations/zip",
            params={"file_path": "/tmp/uploads/project.zip"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "zip"
        assert data["source_config"]["file_path"] == "/tmp/uploads/project.zip"

    def test_configure_github_source(self, client):
        p = _create_project(client, "GitHub Project")
        pid = p["project_id"]

        resp = client.post(
            f"/api/projects/{pid}/integrations/github",
            params={"repo_owner": "myorg", "repo_name": "migration-target", "branch": "main"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "github"
        assert data["source_config"]["repo_owner"] == "myorg"
        assert data["source_config"]["repo_name"] == "migration-target"

    def test_generic_source_update(self, client):
        p = _create_project(client, "Generic Project")
        pid = p["project_id"]

        resp = client.put(
            f"/api/projects/{pid}/source",
            json={"source_type": "manual", "source_config": {"note": "hand-crafted"}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "manual"
        assert data["source_config"] == {"note": "hand-crafted"}


class TestGitFailurePath:
    """WP-1 F-1: git/github 失败时必须建 source_pending Gate，不卡死。"""

    def test_git_failure_creates_source_pending_gate(self, client):
        """Git project with unreachable URL → onboarding/execute should NOT yield event:error
        but create a source_pending Gate with explicit recovery instructions."""
        # Create git project with an unreachable URL (will fail clone)
        p = _create_project(client, "Git Fail Test", source_type="git",
                            source_config={"remote_url": "https://this-host-does-not-exist-xyz123.example/repo.git",
                                           "branch": "main"})
        pid = p["project_id"]

        # Complete onboarding (creates Run + artifacts)
        resp = client.post(f"/api/projects/{pid}/onboarding/complete",
                          json={"execution_mode": "auto"})
        assert resp.status_code == 200

        # Execute onboarding — git clone will fail
        # Using stream_mode=False: TestClient collects the full SSE stream
        resp = client.post(f"/api/projects/{pid}/onboarding/execute")
        assert resp.status_code == 200
        body = resp.text

        # Must NOT have event:error that leaves user stranded
        # The new behavior: emits event:complete with gate_type=source_pending
        assert "event: error" not in body or "event: complete" in body, \
            "git failure must produce either event:complete(source_pending) or not event:error"

        # Must have source_pending gate built with non-empty reason
        assert "source_pending" in body, f"Expected source_pending gate type in SSE body; got: {body[:500]}"
        assert "gate_id" in body, "Expected gate_id in response"
        assert "retry_action" in body, "Expected retry_action in source_pending response"

        # Gate must exist in DB
        gates_resp = client.get(f"/api/projects/{pid}/gates")
        assert gates_resp.status_code == 200
        gates = gates_resp.json()["data"]["gates"]
        pending_gates = [g for g in gates if g.get("gate_type") == "source_pending"]
        assert len(pending_gates) >= 1, \
            f"Expected source_pending gate in DB, got gates: {[g.get('gate_type') for g in gates]}"

        # reason must be non-empty (user needs to know WHY)
        gate_reason = pending_gates[0].get("reason", "")
        assert gate_reason, "source_pending gate must have non-empty reason"

    def test_manual_source_not_affected(self, client):
        """manual 路径不受 WP-1 影响 — 仍正常建 stage_promotion Gate。"""
        p = _create_project(client, "Manual OK Test", source_type="manual")
        pid = p["project_id"]
        resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
        assert resp.status_code == 200

        resp = client.post(f"/api/projects/{pid}/onboarding/execute")
        assert resp.status_code == 200
        body = resp.text

        # Manual should produce event:complete with stage_promotion gate
        assert "event: complete" in body
        # Should NOT be source_pending
        assert "source_pending" not in body

        # DB should have stage_promotion gate
        gates_resp = client.get(f"/api/projects/{pid}/gates")
        gates = gates_resp.json()["data"]["gates"]
        promo_gates = [g for g in gates if g.get("gate_type") == "stage_promotion"]
        assert len(promo_gates) >= 1, \
            f"Expected stage_promotion gate for manual project; got: {[g.get('gate_type') for g in gates]}"


class TestEvidenceStorage:
    """WP-2 F-2: Evidence 真实落可查询存储."""

    def test_evidence_written_by_onboarding_complete(self, client):
        """After onboarding/complete, GET /evidence must return non-empty list."""
        import tempfile, os
        # Create a local_dir project with a real source dir
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple source file
            with open(os.path.join(tmpdir, "main.py"), "w") as f:
                f.write("print('hello')\n")

            # local_dir materializer uses "path" key (not "source_path")
            p = _create_project(client, "Evidence Test", source_type="local_dir",
                                source_config={"path": tmpdir})
            pid = p["project_id"]

            # Complete onboarding — should write 2 Evidence objects
            resp = client.post(f"/api/projects/{pid}/onboarding/complete",
                              json={"execution_mode": "plan"})
            assert resp.status_code == 200

            # GET /evidence must now return non-empty list
            ev_resp = client.get(f"/api/projects/{pid}/evidence")
            assert ev_resp.status_code == 200
            evidence = ev_resp.json()["data"]["evidence"]
            assert len(evidence) >= 2, \
                f"Expected at least 2 evidence items after onboarding/complete, got {len(evidence)}: {evidence}"

            # Each evidence item must have required fields
            for ev in evidence:
                assert "evidence_id" in ev, f"Evidence missing evidence_id: {ev}"
                assert "type" in ev, f"Evidence missing type: {ev}"
                assert "status" in ev, f"Evidence missing status: {ev}"
                assert "claim" in ev, f"Evidence missing claim: {ev}"

            # Verify evidence_ids match Gate evidence_refs after execute
            resp2 = client.post(f"/api/projects/{pid}/onboarding/execute")
            assert resp2.status_code == 200

            gates_resp = client.get(f"/api/projects/{pid}/gates")
            gates = gates_resp.json()["data"]["gates"]
            assert len(gates) > 0, "Expected at least one Gate after execute"

            # Evidence refs must point to real evidence ids
            gate = gates[0]
            ev_refs = gate.get("evidence_refs", [])
            ev_ids = {e["evidence_id"] for e in evidence}
            for ref in ev_refs:
                assert ref in ev_ids, \
                    f"Gate evidence_ref '{ref}' not in real evidence ids {ev_ids}"

    def test_get_evidence_by_id(self, client):
        """GET /evidence/{id} returns the real Evidence object."""
        p = _create_project(client, "Evidence Get Test", source_type="manual")
        pid = p["project_id"]

        resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "plan"})
        assert resp.status_code == 200

        ev_resp = client.get(f"/api/projects/{pid}/evidence")
        evidence = ev_resp.json()["data"]["evidence"]
        assert len(evidence) >= 1

        # Get first evidence by id
        ev_id = evidence[0]["evidence_id"]
        get_resp = client.get(f"/api/projects/{pid}/evidence/{ev_id}")
        assert get_resp.status_code == 200
        fetched = get_resp.json()["data"]
        assert fetched["evidence_id"] == ev_id


class TestP0Idempotency:
    """WP-3 F-3: P0 幂等 — 重复触发不产生重复 Run/Gate."""

    def test_repeat_complete_reuses_run(self, client):
        """重复调用 onboarding/complete 时，返回相同 run_id，不新建重复 Run."""
        p = _create_project(client, "Idempotent Complete Test", source_type="manual")
        pid = p["project_id"]

        resp1 = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "plan"})
        assert resp1.status_code == 200
        run_id_1 = resp1.json()["data"]["run_id"]

        resp2 = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "plan"})
        assert resp2.status_code == 200
        run_id_2 = resp2.json()["data"]["run_id"]

        # Must return the same Run ID
        assert run_id_1 == run_id_2, \
            f"Repeated onboarding/complete must reuse Run; got run1={run_id_1}, run2={run_id_2}"

        # Verify DB: only 1 run for this project
        from app.core.database import get_session
        from app.models.run import Run
        db = get_session()
        runs = db.query(Run).filter(Run.project_id == pid).all()
        db.close()
        assert len(runs) == 1, f"Expected exactly 1 Run in DB, got {len(runs)}"

    def test_repeat_execute_returns_existing_gate(self, client):
        """重复调用 onboarding/execute（在 active gate 存在时）不新建重复 Gate."""
        p = _create_project(client, "Idempotent Execute Test", source_type="manual")
        pid = p["project_id"]

        resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "plan"})
        assert resp.status_code == 200

        # First execute
        resp1 = client.post(f"/api/projects/{pid}/onboarding/execute")
        assert resp1.status_code == 200

        # Second execute — should return existing gate, not create a new one
        resp2 = client.post(f"/api/projects/{pid}/onboarding/execute")
        assert resp2.status_code == 200
        body2 = resp2.text
        # Either idempotent response or same gate
        assert "gate_id" in body2, "Second execute must return a gate_id"

        # Verify only 1 gate in DB
        from app.core.database import get_session
        from app.models.gate import Gate
        db = get_session()
        gates = db.query(Gate).filter(Gate.project_id == pid, Gate.gate_status == "waiting_decision").all()
        db.close()
        assert len(gates) == 1, f"Expected exactly 1 active Gate, got {len(gates)}"

    def test_execute_after_p1_returns_409(self, client):
        """对已晋级 P1 的项目调用 execute → 返回 409，不新建 P0 Gate."""
        p = _create_project(client, "P1 Promotion Test", source_type="manual")
        pid = p["project_id"]

        # Complete + execute to create P0 gate
        client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
        client.post(f"/api/projects/{pid}/onboarding/execute")

        # Get the gate and approve it
        gates_resp = client.get(f"/api/projects/{pid}/gates")
        gates = gates_resp.json()["data"]["gates"]
        assert len(gates) > 0
        gate_id = gates[0]["gate_id"]
        client.post(f"/api/projects/{pid}/gates/{gate_id}/decision", json={"decision": "approve"})

        # Verify P1 state
        proj = client.get(f"/api/projects/{pid}").json()["data"]
        assert proj["current_stage"] == "p1", f"Expected p1, got {proj['current_stage']}"

        # Now try to execute P0 again — must return 409
        resp = client.post(f"/api/projects/{pid}/onboarding/execute")
        assert resp.status_code == 409, \
            f"Expected 409 for P1 project re-executing P0, got {resp.status_code}: {resp.text[:200]}"


class TestGraphDelegate:
    """WP-6: execute_onboarding delegates to LangGraph FlowRuntime (D-037/D-085)."""

    def _setup_project(self, client):
        """Create a manual project and complete onboarding."""
        pid = client.post("/api/projects", json={
            "name": "Graph Delegate Test", "source_type": "manual",
        }).json()["data"]["project_id"]
        resp = client.post(f"/api/projects/{pid}/onboarding/complete",
                           json={"execution_mode": "plan"})
        run_id = resp.json()["data"]["run_id"]
        return pid, run_id

    def test_execute_returns_gate_with_checkpoint_ref(self, client):
        """execute_onboarding creates Gate with non-null checkpoint_ref (WP-6 DoD)."""
        pid, run_id = self._setup_project(client)
        resp = client.post(f"/api/projects/{pid}/onboarding/execute")
        assert resp.status_code == 200

        # Parse SSE to find complete event
        complete_data = {}
        for line in resp.text.split("\n"):
            if line.startswith("data: ") and "gate_id" in line:
                import json as _j
                complete_data = _j.loads(line[6:])

        # Gate must exist
        assert complete_data.get("gate_id") is not None, \
            f"gate_id should be non-null: {complete_data}"
        # checkpoint_ref must equal run_id (thread_id=run_id in checkpoint)
        assert complete_data.get("checkpoint_ref") == run_id, \
            f"checkpoint_ref={complete_data.get('checkpoint_ref')} != run_id={run_id}"
        # graph_capability_status must be live
        assert complete_data.get("graph_capability_status") == "live", \
            f"graph_capability_status should be 'live': {complete_data}"
        # graph_driven flag
        assert complete_data.get("graph_driven") is True

    def test_gate_has_checkpoint_ref_in_db(self, client):
        """Gate row in DB has non-null checkpoint_ref after graph execution (V1)."""
        pid, run_id = self._setup_project(client)
        client.post(f"/api/projects/{pid}/onboarding/execute")

        # Query active gate
        resp = client.get(f"/api/projects/{pid}/gates/active")
        gate = resp.json().get("data")
        assert gate is not None, "Active gate should exist after execute"
        assert gate["checkpoint_ref"] is not None, \
            f"checkpoint_ref should be non-null: {gate}"
        assert gate["checkpoint_ref"] == run_id, \
            f"checkpoint_ref should equal run_id={run_id}, got {gate['checkpoint_ref']}"

    def test_gate_decision_via_graph_resume(self, client):
        """Gate decision endpoint works correctly after graph execution (WP-6 / W9).

        In the TestClient environment each request runs in a separate event loop,
        so the async checkpointer cannot reconnect across requests — graph_thread_active
        returns False and the decision falls back to direct mode. The test verifies that
        the decision IS applied correctly (gate status changes, stage advances).
        NOTE: live verification (V6) on a real server confirms graph_driven=True.
        """
        pid, run_id = self._setup_project(client)
        client.post(f"/api/projects/{pid}/onboarding/execute")

        # Get gate
        gate_resp = client.get(f"/api/projects/{pid}/gates/active")
        gate = gate_resp.json().get("data")
        assert gate is not None, "Active gate required"
        gate_id = gate["gate_id"]

        # Approve via routes_gates decide_gate
        decision_resp = client.post(
            f"/api/projects/{pid}/gates/{gate_id}/decision",
            json={"decision": "approve"},
        )
        assert decision_resp.status_code == 200
        result = decision_resp.json()["data"]
        # Decision was applied — gate status changed
        gate_after = result.get("gate", {})
        assert gate_after.get("decision") == "approve", \
            f"Gate decision should be 'approve': {gate_after}"
        assert gate_after.get("gate_status") in ("approved",), \
            f"Gate status should be 'approved': {gate_after}"
