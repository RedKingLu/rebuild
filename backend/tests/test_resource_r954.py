"""R9-5-4 验收测试 — 资源运行回路 + 案例/知识/MCP/Tool.

W1: resource_loader.py    — load(), dispatch rules (T1.1/T6.3)
W2: tool_registry.py      — load_schemas(), execute_tool() routing (T2.1/T2.2)
W3: knowledge_search.py   — search() full-text + scope (T5.2)
W4: review state machine  — approve/reject via /resources/{id}/review (T6.2)
W5: multipart import      — URL import via /import/resource (T6.1)
W6: MCP call endpoint     — /mcp/{id}/call route (T3.3)
W7: resource_resolver.py  — three-tier: self→local→online (T7.1)
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


def test_resource_loader_rule3_community_unreviewed():
    """Rule 3 (D-061/T6.3): community source with unreviewed trust → not schedulable."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(
            db, resource_type="knowledge",
            source_type="community",
            trust_level="unreviewed",
        )
        result = load(entry.resource_id, db)
        assert result is not None
        assert result.schedulable is False
        assert "review_required" in result.reason
    finally:
        db.close()


def test_resource_loader_rule3_community_approved():
    """Rule 3 pass: community resource with reviewed_reference trust IS schedulable."""
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
# W4 — Review state machine (T6.2) via HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _create_community_resource(client) -> str:
    """Helper: create a community resource in draft/unreviewed state via API."""
    resp = client.post("/api/resources", json={
        "name": "Test Community Resource",
        "resource_type": "knowledge",
        "source_type": "community",
        "source_trust_level": "unreviewed",
        "risk_level": "L0",
        "status": "draft",
        "description": "Test community doc for review",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["resource_id"]


def test_review_approve(client):
    """POST /api/resources/{id}/review approve → status=active, trust=reviewed_reference."""
    rid = _create_community_resource(client)
    resp = client.post(f"/api/resources/{rid}/review", json={
        "decision": "approve",
        "reviewer": "test_reviewer",
        "review_notes": "Looks good.",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True or body.get("status") == "success"

    # Verify the resource state changed
    get_resp = client.get(f"/api/resources/{rid}")
    assert get_resp.status_code == 200, get_resp.text
    resource = get_resp.json()
    assert resource["status"] == "active"
    assert resource["source_trust_level"] == "reviewed_reference"


def test_review_reject(client):
    """POST /api/resources/{id}/review reject → status=blocked, trust=blocked."""
    rid = _create_community_resource(client)
    resp = client.post(f"/api/resources/{rid}/review", json={
        "decision": "reject",
        "reviewer": "test_reviewer",
        "review_notes": "Not appropriate.",
    })
    assert resp.status_code == 200, resp.text

    get_resp = client.get(f"/api/resources/{rid}")
    resource = get_resp.json()
    assert resource["status"] == "blocked"
    assert resource["source_trust_level"] == "blocked"


def test_review_invalid_decision(client):
    """POST /api/resources/{id}/review with invalid decision → 400."""
    rid = _create_community_resource(client)
    resp = client.post(f"/api/resources/{rid}/review", json={"decision": "maybe"})
    assert resp.status_code == 400


def test_review_unknown_resource(client):
    """POST /api/resources/unknown-id/review → 404."""
    resp = client.post("/api/resources/does-not-exist/review", json={"decision": "approve"})
    assert resp.status_code == 404


def test_review_approved_community_becomes_schedulable(client):
    """After approve, community resource passes Rule 3 in resource_loader."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    rid = _create_community_resource(client)
    # Before approval: should not be schedulable
    db = get_session()
    try:
        before = load(rid, db)
        assert before is not None
        assert before.schedulable is False
    finally:
        db.close()

    # Approve
    client.post(f"/api/resources/{rid}/review", json={"decision": "approve", "reviewer": "test"})

    # After approval: should be schedulable
    db = get_session()
    try:
        after = load(rid, db)
        assert after is not None
        assert after.schedulable is True
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
# W7 — resource_resolver.py three-tier (T7.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_resolver_online_tier_no_db():
    """With no DB, resolve_many returns online static resources."""
    from app.services.resource_resolver import resolve_many
    results = resolve_many(resource_types=None, stage="p0", project_id=None, db=None)
    assert len(results) > 0
    online = [r for r in results if r.hit_tier == "online"]
    assert len(online) > 0


def test_resolver_online_tier_not_schedulable():
    """Online-tier resources must not be schedulable (require import first, D-089)."""
    from app.services.resource_resolver import resolve_many
    results = resolve_many(resource_types=None, stage="p0", project_id=None, db=None)
    for r in results:
        if r.hit_tier == "online":
            assert r.schedulable is False, (
                f"Online resource {r.resource_id} should not be schedulable"
            )
            assert r.dispatch == "online_import_required"


def test_resolver_local_tier_with_db():
    """With DB, resolve_many includes local resources tagged with hit_tier='local'."""
    from app.core.database import get_session
    from app.services.resource_resolver import resolve_many
    db = get_session()
    try:
        # Seed a local tool resource
        _make_entry(db, resource_type="tool")
        results = resolve_many(resource_types=["tool"], stage="p0", project_id=None, db=db)
        local = [r for r in results if r.hit_tier == "local"]
        assert len(local) >= 1
    finally:
        db.close()


def test_resolver_hit_tier_field_always_set():
    """All resolved resources have hit_tier set to 'self', 'local', or 'online'."""
    from app.core.database import get_session
    from app.services.resource_resolver import resolve_many
    db = get_session()
    try:
        results = resolve_many(stage="p0", project_id=None, db=db)
        for r in results:
            assert r.hit_tier in ("self", "local", "online"), (
                f"Unexpected hit_tier: {r.hit_tier}"
            )
    finally:
        db.close()


def test_resolver_type_filter():
    """resolve_many respects resource_types filter."""
    from app.core.database import get_session
    from app.services.resource_resolver import resolve_many
    db = get_session()
    try:
        results = resolve_many(resource_types=["tool"], stage="p0", project_id=None, db=db)
        for r in results:
            if r.hit_tier == "local":
                assert r.resource_type == "tool", (
                    f"Expected resource_type=tool for local, got {r.resource_type}"
                )
    finally:
        db.close()


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


def test_t63_community_unreviewed_blocked_from_loader():
    """T6.3: community resource with unreviewed trust must fail resource_loader Rule 3."""
    from app.core.database import get_session
    from app.services.resource_loader import load
    db = get_session()
    try:
        entry = _make_entry(
            db, resource_type="knowledge",
            source_type="community",
            trust_level="unreviewed",
        )
        loaded = load(entry.resource_id, db)
        assert loaded is not None
        assert loaded.schedulable is False
        assert "review_required" in loaded.reason
    finally:
        db.close()


def test_online_source_provider_returns_six_cards():
    """online_source_provider returns exactly 6 static community cards (D-089)."""
    from app.services.online_source_provider import get_static_online_resources
    cards = get_static_online_resources()
    assert len(cards) == 6, f"Expected 6 static cards, got {len(cards)}"


def test_online_source_provider_all_tagged_static_r15():
    """All online cards are tagged online_source='static_R15'."""
    from app.services.online_source_provider import get_static_online_resources
    cards = get_static_online_resources()
    for c in cards:
        assert c.get("online_source") == "static_R15", (
            f"Card {c.get('resource_id')} missing static_R15 tag"
        )


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


def test_resource_resolver_module_public_api():
    """resource_resolver.py exports the expected public API (resolve, resolve_many)."""
    import app.services.resource_resolver as mod
    assert hasattr(mod, "resolve")
    assert hasattr(mod, "resolve_many")
    assert hasattr(mod, "ResolvedResource")
