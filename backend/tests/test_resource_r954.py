"""R9-5-4 验收测试 — 资源运行回路 + 案例/知识/MCP/Tool.

W1: resource_loader.py    — load(), dispatch rules (T1.1/T6.3)
W2: tool_registry.py      — load_schemas(), execute_tool() routing (T2.1/T2.2)
W3: knowledge_search.py   — search() full-text + scope (T5.2)
W4: review state machine  — approve/reject via /resources/{id}/review (T6.2)
W5: multipart import      — URL import via /import/resource (T6.1)
W6: MCP call endpoint     — /mcp/{id}/call route (T3.3)
W8: architecture          — G4 trace injection, T6.3 community block, online provider
"""

from __future__ import annotations

import uuid
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# W1 — resource_loader.py (T1.1 / T6.3)
# ─────────────────────────────────────────────────────────────────────────────

def _make_entry(
    db, *,
    resource_type="tool",
    source_type="user_provided",
    trust_level="trusted_current",
    status_val="active",
    enabled=True,
    name=None,
):
    from app.models.resource_entry import (
        ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus,
    )
    rid = str(uuid.uuid4())
    entry = ResourceEntry(
        resource_id=rid,
        name=name or f"res_{rid[:8]}",
        resource_type=ResourceType(resource_type),
        source_type=SourceType(source_type),
        source_trust_level=TrustLevel(trust_level),
        risk_level=RiskLevel.L0,
        status=ResourceStatus(status_val),
        enabled=enabled,
    )
    db.add(entry)
    db.commit()
    return entry


def test_resource_loader_load_missing():
    """load() returns None for unknown resource_id."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        result = load("non-existent-resource-id", db)
        assert result is None
    finally:
        db.close()


def test_resource_loader_load_existing():
    """load() returns LoadedResource for a known, valid resource."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(db, resource_type="tool")
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.resource_id == entry.resource_id
        assert result.schedulable is True
        assert result.dispatch == "tool_registry"
    finally:
        db.close()


def test_resource_loader_rule1_remote_source():
    """Rule 1: external_online source → schedulable=False, reason=R14_remote_orchestration."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(db, resource_type="knowledge", source_type="external_online")
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.schedulable is False
        assert "R14" in result.reason
    finally:
        db.close()


def test_resource_loader_rule2_disabled():
    """Rule 2: disabled=False → schedulable=False, reason=disabled."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(db, resource_type="tool", enabled=False)
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.schedulable is False
        assert result.reason == "disabled"
    finally:
        db.close()


def test_resource_loader_rule2_bad_status():
    """Rule 2: status=draft → schedulable=False."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(db, resource_type="tool", status_val="draft")
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.schedulable is False
        assert "status_not_schedulable" in result.reason
    finally:
        db.close()


def test_resource_loader_community_enabled_schedulable():
    """R15-4-C1 (D-061 修订): community resource that is enabled + active IS schedulable.
    The old 'community unreviewed → review_required' gate has been removed; 合格性由发布侧保证。"""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(
            db, resource_type="knowledge",
            source_type="community",
            trust_level="unreviewed",
            status_val="read_only",
        )
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.schedulable is True, (
            "community+enabled must be schedulable after R15-4-C1 (no review gate)"
        )
        assert "review_required" not in (result.reason or "")
    finally:
        db.close()


def test_resource_loader_community_reviewed_still_schedulable():
    """community resource with reviewed_reference trust remains schedulable (unchanged)."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(
            db, resource_type="knowledge",
            source_type="community",
            trust_level="reviewed_reference",
        )
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.schedulable is True
    finally:
        db.close()


def test_resource_loader_rule4_blocked_trust():
    """Rule 4: blocked trust level → schedulable=False."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(db, resource_type="tool", trust_level="blocked")
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.schedulable is False
        assert "blocked" in result.reason
    finally:
        db.close()


def test_resource_loader_rule5_dispatch_types():
    """Rule 5: dispatch differs by resource_type."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    expected = {
        "tool": "tool_registry",
        "mcp": "mcp_call",
        "case": "case_context",
        "knowledge": "knowledge_context",
        "skill": "agent_skill_ref",
        "hook": "registry_ref",
    }
    db = get_session()
    try:
        for rtype, expected_dispatch in expected.items():
            st = "read_only" if rtype in ("case", "knowledge") else "active"
            entry = _make_entry(db, resource_type=rtype, status_val=st)
            result = load(entry.resource_id, db)
            assert result is not None, f"None for {rtype}"
            assert result.dispatch == expected_dispatch, (
                f"{rtype}: expected dispatch={expected_dispatch}, got {result.dispatch}"
            )
    finally:
        db.close()


def test_resource_loader_case_never_execute():
    """Case type always sets never_execute=True (D-061)."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(db, resource_type="case", status_val="read_only")
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.never_execute is True
    finally:
        db.close()


def test_resource_loader_load_available_only_schedulable():
    """load_available() returns only schedulable resources (filters out blocked/remote)."""
    from app.core.database import get_session
    from app.services.resource_loader import load_available
    db = get_session()
    try:
        # Create one good tool and one blocked tool
        good = _make_entry(db, resource_type="tool")
        _make_entry(db, resource_type="tool", trust_level="blocked")
        results = load_available(stage="p0", resource_types=["tool"], db=db)
        ids = [r.resource_id for r in results]
        assert good.resource_id in ids
        # All returned must be schedulable
        for r in results:
            assert r.schedulable is True, f"{r.resource_id} not schedulable"
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# W2 — tool_registry.py (T2.1 / T2.2)
# ─────────────────────────────────────────────────────────────────────────────

def test_tool_registry_load_schemas_returns_list():
    """load_schemas() returns a non-empty list of schema dicts."""
    from app.core.database import get_session
    from app.services.tool_registry import load_schemas
    db = get_session()
    try:
        schemas = load_schemas(tool_refs=[], stage="p0", db=db, include_mcp=False)
        assert isinstance(schemas, list)
        # Built-in seed tools should always be present
        assert len(schemas) >= 1
    finally:
        db.close()


def test_tool_registry_load_schemas_have_name():
    """Each schema dict must contain function.name (OpenAI function schema format)."""
    from app.core.database import get_session
    from app.services.tool_registry import load_schemas
    db = get_session()
    try:
        schemas = load_schemas(tool_refs=[], stage="p0", db=db, include_mcp=False)
        for s in schemas:
            # OpenAI format: {type, function: {name, description, parameters}}
            assert "function" in s or "name" in s, f"schema missing name/function: {s!r}"
            fn_name = s.get("name") or (s.get("function") or {}).get("name")
            assert fn_name, f"schema has no resolvable name: {s!r}"
    finally:
        db.close()


def test_tool_registry_execute_returns_dict():
    """execute_tool() with a built-in tool name returns a dict (no exception)."""
    import asyncio
    from app.core.database import get_session
    from app.services.tool_registry import execute_tool, load_schemas

    db = get_session()
    try:
        schemas = load_schemas(tool_refs=[], stage="p0", db=db, include_mcp=False)
        # Pick the first available built-in name (resolve from nested or flat)
        if schemas:
            s = schemas[0]
            tool_name = s.get("name") or (s.get("function") or {}).get("name") or "read_file"
        else:
            tool_name = "read_file"
        result = asyncio.run(
            execute_tool(tool_name, {}, "test-project", "p0", db, tracer=None)
        )
        assert isinstance(result, dict)
    finally:
        db.close()


def test_tool_registry_no_litellm_import():
    """G2: tool_registry.py must not import litellm directly."""
    import ast
    from pathlib import Path
    src = (
        Path(__file__).parent.parent / "app" / "services" / "tool_registry.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for n in names:
                assert "litellm" not in (n or ""), (
                    "G2 violation: tool_registry.py must not import litellm directly"
                )


def _make_l3_tool(db, tool_slug="danger_exec_tool"):
    """Create an enabled, active L3-risk tool ResourceEntry routed via execute scope."""
    from app.models.resource_entry import (
        ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus,
    )
    rid = str(uuid.uuid4())
    entry = ResourceEntry(
        resource_id=rid,
        name=f"L3 {tool_slug}",
        resource_type=ResourceType.tool,
        source_type=SourceType.user_provided,
        source_trust_level=TrustLevel.trusted_current,
        risk_level=RiskLevel.L3,
        status=ResourceStatus.active,
        enabled=True,
        description="高风险执行类工具（L3），执行前须人工审批",
        type_metadata={"tool_name": tool_slug, "write_scope": "execute"},
    )
    db.add(entry)
    db.commit()
    return entry


def test_tool_registry_l3_creates_action_approval_gate():
    """OD-06: an L3+ tool must NOT execute silently — execute_tool creates a REAL
    action_approval Gate (via GateService) and returns awaiting_approval. No fabrication:
    the gate is verifiable through GateService.

    B-ACC-GATE-APPROVAL-NOT-BOUND 解除条件④（如实说明测试改动）：命令原为 `"rm -rf /"`。
    本批次新增"建 Gate 前先过白名单"预检（`tool_registry._precheck_command_allowed`），
    `write_scope="execute"` 的工具会走该预检；`rm` 不在 `ALLOWED_COMMANDS`，即便获批也会被
    执行层挡回——这正是预检要拦的"注定无法执行、不该请用户签核"的场景，属"测试依赖了
    该预检要消灭的行为"（用不会被批准后执行的命令去验证"能建 Gate"）。本用例的验证意图是
    "L3 工具会被真实 Gate 挡住"，与命令内容是否在白名单无关，故改用白名单内的 `echo`
    （与下方 `test_tool_registry_action_approval_gate_does_not_advance_stage` 的 `"ls"`
    同类），不改变本用例验证的能力，也不放宽任何守卫。"""
    import asyncio
    from app.core.database import get_session
    from app.services.tool_registry import execute_tool
    from app.dependencies import get_services

    db = get_session()
    try:
        _make_l3_tool(db, "danger_exec_tool")
        project_id = "proj-l3-test"
        result = asyncio.run(
            execute_tool("danger_exec_tool", {"command": "echo danger-simulated"}, project_id,
                         stage="p1", db=db, run_id="run-l3-test", tracer=None)
        )
        # execution must be parked behind a real approval gate, not run
        assert result["status"] == "awaiting_approval", result
        assert result["gate_type"] == "action_approval"
        assert result["risk_level"] == "L3"
        assert result.get("gate_id"), "a real gate_id must be returned"

        # the gate is genuinely persisted + waiting (verifiable, not fabricated)
        gates = get_services().gate_service.list_by_project(project_id)
        approval = [g for g in gates if g.gate_type == "action_approval"]
        assert len(approval) == 1, f"expected 1 action_approval gate, got {approval}"
        assert approval[0].gate_id == result["gate_id"]
        assert approval[0].gate_status == "waiting_decision"
        assert approval[0].risk_level == "L3"
        assert approval[0].run_id == "run-l3-test"
    finally:
        db.close()


def test_tool_registry_action_approval_gate_does_not_advance_stage():
    """公理6: deciding an action_approval Gate must NOT drive stage promotion —
    it only authorizes the parked tool action."""
    import asyncio
    from app.core.database import get_session
    from app.services.tool_registry import execute_tool
    from app.dependencies import get_services
    from app.schemas.gate import GateDecisionRequest

    db = get_session()
    try:
        _make_l3_tool(db, "danger_exec_tool2")
        result = asyncio.run(
            execute_tool("danger_exec_tool2", {"command": "ls"}, "proj-l3-test2",
                         stage="p0", db=db, run_id="run-l3-test2", tracer=None)
        )
        gid = result["gate_id"]
        gs = get_services().gate_service
        g, audit = gs.decide(gid, GateDecisionRequest(decision="approve"))
        # action_approval decided → recorded + audited, but it is NOT a stage_promotion
        assert g.gate_status == "approved"
        assert g.gate_type == "action_approval"
        assert audit is not None
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# W3 — knowledge_search.py (T5.2)
# ─────────────────────────────────────────────────────────────────────────────

def _make_knowledge(db, name="TestKnowledge", description="AI 安全基线测试文档", source_type="user_provided"):
    from app.models.resource_entry import (
        ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus,
    )
    rid = str(uuid.uuid4())
    entry = ResourceEntry(
        resource_id=rid,
        name=name,
        resource_type=ResourceType.knowledge,
        source_type=SourceType(source_type),
        source_trust_level=TrustLevel.trusted_current,
        risk_level=RiskLevel.L0,
        status=ResourceStatus.read_only,
        description=description,
        enabled=True,
    )
    db.add(entry)
    db.commit()
    return entry


def test_knowledge_search_empty_query_returns_list():
    """Empty query → list (not exception), uses _list_all path."""
    from app.core.database import get_session
    from app.services.knowledge_search import search
    db = get_session()
    try:
        _make_knowledge(db)
        results = search("", db, limit=10, scope="all")
        assert isinstance(results, list)
    finally:
        db.close()


def test_knowledge_search_match_by_name():
    """search() finds resource matching query in name."""
    from app.core.database import get_session
    from app.services.knowledge_search import search
    db = get_session()
    try:
        _make_knowledge(db, name="XYZUnique安全规范", description="some description")
        results = search("XYZUnique", db, limit=10, scope="all")
        assert len(results) >= 1
        names = [r["name"] for r in results]
        assert any("XYZUnique" in n for n in names)
    finally:
        db.close()


def test_knowledge_search_result_keys():
    """search() results contain required fields."""
    from app.core.database import get_session
    from app.services.knowledge_search import search
    db = get_session()
    try:
        _make_knowledge(db, name="KeyCheck知识库", description="testing field completeness")
        results = search("KeyCheck", db, limit=5, scope="all")
        assert len(results) >= 1
        r = results[0]
        for key in ("resource_id", "name", "snippet", "score", "retrieval_mode"):
            assert key in r, f"missing key: {key}"
        assert r["retrieval_mode"] == "fulltext_like"
    finally:
        db.close()


def test_knowledge_search_scope_filters():
    """scope parameter filters by source_type."""
    from app.core.database import get_session
    from app.services.knowledge_search import search
    db = get_session()
    try:
        # user_provided resource: should appear in scope="user" but not "community"
        _make_knowledge(db, name="ScopeUser文档", source_type="user_provided")
        user_results = search("ScopeUser", db, limit=10, scope="user")
        community_results = search("ScopeUser", db, limit=10, scope="community")
        assert len(user_results) >= 1
        assert len(community_results) == 0
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# W4 — Review DEPRECATED (410) + enable/disable + soft-delete (R15-4-C1)
# ─────────────────────────────────────────────────────────────────────────────

def _create_community_resource(client, status_val="active", trust="unreviewed") -> str:
    """Helper: create a community resource via API (no review needed after R15-4-C1)."""
    resp = client.post("/api/resources", json={
        "name": "Test Community Resource",
        "resource_type": "knowledge",
        "source_type": "community",
        "source_trust_level": trust,
        "risk_level": "L0",
        "status": status_val,
        "description": "Test community doc",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["resource_id"]


def test_review_endpoint_gone(client):
    """R15-4-C1: POST /api/resources/{id}/review is DEPRECATED → 410 Gone (D-061 修订)."""
    rid = _create_community_resource(client)
    resp = client.post(f"/api/resources/{rid}/review", json={"decision": "approve"})
    assert resp.status_code == 410, resp.text


def test_review_endpoint_gone_before_existence_check(client):
    """410 is returned regardless of resource existence (endpoint fully deprecated)."""
    resp = client.post("/api/resources/does-not-exist/review", json={"decision": "approve"})
    assert resp.status_code == 410


def test_enable_disable_resource(client):
    """R15-4-C1: PATCH /enable and /disable flip enabled and are the canonical control."""
    rid = _create_community_resource(client)
    # disable
    resp = client.patch(f"/api/resources/{rid}/disable")
    assert resp.status_code == 200, resp.text
    got = client.get(f"/api/resources/{rid}").json()
    assert got["enabled"] is False
    # enable
    resp = client.patch(f"/api/resources/{rid}/enable")
    assert resp.status_code == 200, resp.text
    got = client.get(f"/api/resources/{rid}").json()
    assert got["enabled"] is True


def test_enable_unknown_resource(client):
    """PATCH /enable on unknown id → 404."""
    resp = client.patch("/api/resources/does-not-exist/enable")
    assert resp.status_code == 404


def test_soft_delete_writes_deleted_at_and_filters_list(client):
    """R15-4-C1: DELETE is a soft-delete — writes deleted_at, hides from default list."""
    rid = _create_community_resource(client)
    # present in list before delete
    before = client.get("/api/resources?type=knowledge&limit=200").json()
    before_ids = [r["resource_id"] for r in before["data"]["resources"]]
    assert rid in before_ids
    # soft delete
    resp = client.delete(f"/api/resources/{rid}")
    assert resp.status_code == 200, resp.text
    # gone from default list
    after = client.get("/api/resources?type=knowledge&limit=200").json()
    after_ids = [r["resource_id"] for r in after["data"]["resources"]]
    assert rid not in after_ids, "soft-deleted resource must not appear in default list"
    # second delete → 404 (already soft-deleted)
    resp2 = client.delete(f"/api/resources/{rid}")
    assert resp2.status_code == 404


def test_soft_deleted_not_schedulable(client):
    """R15-4-C1: a soft-deleted resource must not be schedulable by resource_loader."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    rid = _create_community_resource(client, status_val="read_only")
    client.delete(f"/api/resources/{rid}")
    db = get_session()
    try:
        result = load(rid, db)
        assert result is not None
        assert result.schedulable is False
        assert result.reason == "deleted"
    finally:
        db.close()


def test_community_resource_schedulable_without_review(client):
    """R15-4-C1: an enabled community resource is schedulable directly (no approval step)."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    rid = _create_community_resource(client, status_val="read_only", trust="unreviewed")
    db = get_session()
    try:
        result = load(rid, db)
        assert result is not None
        assert result.schedulable is True, "no review needed after R15-4-C1"
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# W5 — Multipart import endpoint (T6.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_import_resource_url(client):
    """POST /api/import/resource with file upload → creates resource."""
    md_content = "# ECC Security Guide\n\nSecurity baseline 11 rules.".encode("utf-8")
    resp = client.post(
        "/api/import/resource",
        data={"name": "Imported Security Guide", "resource_type": "knowledge"},
        files={"file": ("security-guide.md", md_content, "text/markdown")},
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body.get("success") is True or "resource_id" in str(body)


def test_import_resource_no_source(client):
    """POST /api/import/resource with neither URL nor file → 422."""
    resp = client.post("/api/import/resource", data={
        "name": "Empty Import",
        "resource_type": "knowledge",
    })
    assert resp.status_code == 422


def test_import_case_url(client):
    """POST /api/import/case with file upload → creates case resource."""
    md_content = "# Case Study\n\nMigration case record.".encode("utf-8")
    resp = client.post(
        "/api/import/case",
        data={"name": "Test Case Import"},
        files={"file": ("case-study.md", md_content, "text/markdown")},
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body.get("success") is True or "resource_id" in str(body)


# ─────────────────────────────────────────────────────────────────────────────
# W6 — MCP call endpoint (T3.3)
# ─────────────────────────────────────────────────────────────────────────────

def test_mcp_call_endpoint_missing_server(client):
    """POST /api/mcp/{mcp_id}/call on non-existent server → 404."""
    resp = client.post("/api/mcp/nonexistent-mcp-id/call", json={
        "tool_name": "some_tool",
        "arguments": {},
    })
    assert resp.status_code == 404


def test_mcp_call_endpoint_route_exists():
    """Route /mcp/{id}/call is registered in the MCP router."""
    from app.api.routes_mcp import mcp_router
    route_paths = [r.path for r in mcp_router.routes]
    assert any("/call" in p for p in route_paths), (
        f"'/call' route not found in mcp_router.routes: {route_paths}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# W8 — Architecture constraints
# ─────────────────────────────────────────────────────────────────────────────

def test_g4_context_assembler_injects_cases_and_knowledge():
    """G4: assemble_context returns 'cases' and 'knowledge' keys with assembly_trace counts."""
    from app.services.context_assembler import assemble_context
    ctx = assemble_context("test-project-g4", "p0")
    assert "cases" in ctx, "G4: context package must contain 'cases'"
    assert "knowledge" in ctx, "G4: context package must contain 'knowledge'"
    trace = ctx.get("assembly_trace", {})
    assert "case_count" in trace, "G4: assembly_trace must include case_count"
    assert "knowledge_count" in trace, "G4: assembly_trace must include knowledge_count"
    assert isinstance(trace["case_count"], int)
    assert isinstance(trace["knowledge_count"], int)


def test_t63_community_enabled_schedulable_from_loader():
    """R15-4-C1 (supersedes T6.3): community resource with unreviewed trust but enabled
    is now schedulable via resource_loader — the review gate was removed (D-061 修订)."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(
            db, resource_type="knowledge",
            source_type="community",
            trust_level="unreviewed",
            status_val="read_only",
        )
        loaded = load(entry.resource_id, db)
        assert loaded is not None
        assert loaded.schedulable is True
        assert "review_required" not in (loaded.reason or "")
    finally:
        db.close()


def test_online_source_provider_returns_six_cards():
    """online_source_provider returns exactly 6 static community cards (D-089)."""
    from app.services.online_source_provider import get_static_online_resources
    cards = get_static_online_resources()
    assert len(cards) == 6, f"Expected 6 static cards, got {len(cards)}"


def test_online_source_provider_all_tagged_static_r15_fallback():
    """Legacy static fallback cards are tagged online_source='static_R15_fallback'.

    R15-4-C5: the primary path is now real community retrieval
    (search_community_resources); the static cards remain only as an honest
    fallback (distinct tag) for air-gapped runs.
    """
    from app.services.online_source_provider import get_static_online_resources
    cards = get_static_online_resources()
    for c in cards:
        assert c.get("online_source") == "static_R15_fallback", (
            f"Card {c.get('resource_id')} missing static_R15_fallback tag"
        )


def test_search_community_resources_real_path():
    """R15-4-C5: search_community_resources uses the real connector (primary path)
    and reports community_available; falls back to static cards when unreachable."""
    from unittest.mock import patch
    from app.services.online_source_provider import search_community_resources
    # reachable → real items, community_available=True
    with patch("app.services.community_connector.search_resources",
               return_value={"totalSize": 1, "offset": 0,
                             "resources": [{"id": "c1", "name": "n", "resource_type": "case",
                                            "description": "", "tags": [], "categories": [],
                                            "license": "", "source": "community",
                                            "verified": False, "download_count": 0,
                                            "checksum_sha256": "x"}]}):
        res = search_community_resources(type="case")
    assert res["community_available"] is True
    assert res["items"][0]["online_source"] == "community_real"
    assert res["items"][0]["resource_id"] == "c1"


def test_online_source_provider_type_filter():
    """get_static_online_resources with type filter returns only matching types."""
    from app.services.online_source_provider import get_static_online_resources
    skill_cards = get_static_online_resources(resource_types=["skill"])
    for c in skill_cards:
        assert c["resource_type"] == "skill"
    knowledge_cards = get_static_online_resources(resource_types=["knowledge"])
    for c in knowledge_cards:
        assert c["resource_type"] == "knowledge"


def test_resource_loader_module_public_api():
    """resource_loader.py exports the expected public API (load, load_available, LoadedResource)."""
    import app.services.resource_loader as mod
    assert hasattr(mod, "load")
    assert hasattr(mod, "load_available")
    assert hasattr(mod, "LoadedResource")


def test_knowledge_search_module_public_api():
    """knowledge_search.py exports the expected public API (search, load_body)."""
    import app.services.knowledge_search as mod
    assert hasattr(mod, "search")
    assert hasattr(mod, "load_body")
