"""R16-E2: /api/knowledge/search HTTP route contract (frontend wiring verification).

Verifies the real backend full-text search endpoint that KnowledgePage now calls
(replacing the R15 static frontend-only filter — red line #4 remediation).

Route: GET /api/knowledge/search?q=&scope=&limit=  (mounted via registry_router)
Backend: app.services.knowledge_search.search() — LIKE full-text over name +
         description + on-disk body_path content, ranked by score, snippet extract.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _restore_settings():
    """Restore global settings mutated by _setup in this module.

    _setup redirects the frozen global cfg.settings (data_dir / source_dir /
    database_url) via object.__setattr__ but had no teardown, leaving source_dir
    pointing at a deleted tmp_path after the test — polluting later tests that read
    settings.source_path (e.g. skill_loader disk fallback). Mirror conftest
    isolated_data's _orig_* save/restore pattern: snapshot before the test body
    mutates, restore on teardown. Runs after conftest's autouse isolated_data
    (conftest fixtures set up first, tear down last), so the chain restores
    correctly to true originals.
    """
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    _orig_data = cfg.settings.data_dir
    _orig_source = cfg.settings.source_dir
    _orig_db = cfg.settings.database_url
    yield
    object.__setattr__(cfg.settings, "data_dir", _orig_data)
    object.__setattr__(cfg.settings, "source_dir", _orig_source)
    object.__setattr__(cfg.settings, "database_url", _orig_db)
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()


def _setup(monkeypatch, tmp_path):
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "data"))
    object.__setattr__(cfg.settings, "source_dir", str(tmp_path / "source"))
    object.__setattr__(cfg.settings, "database_url", f"sqlite:///{tmp_path / 'rebuild.db'}")
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()


def _make_knowledge_body(db, tmp_path, name, body_text, source_type="seed"):
    """Create a Knowledge ResourceEntry with real on-disk Markdown body."""
    from app.models.resource_entry import ResourceEntry, ResourceStatus, SourceType, TrustLevel, RiskLevel
    src_dir = tmp_path / "source" / "knowledge" / f"doc-{name}"
    src_dir.mkdir(parents=True, exist_ok=True)
    body_file = src_dir / "body.md"
    body_file.write_text(body_text, encoding="utf-8")
    entry = ResourceEntry(
        name=name, resource_type="knowledge",
        description=f"知识库文档：{name}", version="1.0.0",
        source_type=SourceType.internal_current if source_type == "seed" else SourceType.user_provided,
        source_trust_level=TrustLevel.read_only_reference,
        risk_level=RiskLevel.L0, status=ResourceStatus.active, enabled=True,
        source_path_or_ref=str(src_dir),
        capabilities={"tags": ["R16测试"]},
        type_metadata={"body_path": str(body_file), "knowledge_package": "r16-docs",
                     "body_parse_status": "ok", "package_version": "1.0.0"},
    )
    db.add(entry); db.flush(); db.refresh(entry)
    return entry.resource_id


def test_search_route_empty_query_returns_envelope(tmp_path, monkeypatch):
    """Empty q → 200 + SuccessEnvelope with results list."""
    _setup(monkeypatch, tmp_path)
    from app.main import app
    c = TestClient(app)
    r = c.get("/api/knowledge/search?q=&scope=all")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]["query"] == ""
    assert isinstance(body["data"]["results"], list)
    assert body["meta"]["source_status"] == "real"


def test_search_route_matches_body_content(tmp_path, monkeypatch):
    """Red line #4 remediation: backend search finds body content that frontend
    static filter could never reach (name/description don't contain the token)."""
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    try:
        _make_knowledge_body(db, tmp_path, "信创迁移实战",
                             "# 信创迁移实战\n\n本文讲解操作系统麒麟Kylin与数据库达梦的迁移要点。\n\n正文正文唯一匹配词 R16UNIQUETOKEN。")
        db.commit()
    finally:
        db.close()

    from app.main import app
    c = TestClient(app)
    # Token only in body, NOT in name/description
    r = c.get("/api/knowledge/search?q=R16UNIQUETOKEN&scope=all")
    assert r.status_code == 200, r.text
    results = r.json()["data"]["results"]
    assert len(results) >= 1, "backend body search must match body-path content"
    assert any("信创迁移实战" in res["name"] for res in results)
    assert "R16UNIQUETOKEN" in results[0].get("snippet", "")


def test_search_route_result_contract(tmp_path, monkeypatch):
    """Frontend wiring contract: every result has the fields KnowledgePage reads."""
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    try:
        _make_knowledge_body(db, tmp_path, "契约检查文档", "# API 契约\n\n详细内容。")
        db.commit()
    finally:
        db.close()

    from app.main import app
    c = TestClient(app)
    r = c.get("/api/knowledge/search?q=契约&scope=all")
    assert r.status_code == 200, r.text
    results = r.json()["data"]["results"]
    assert len(results) >= 1
    res = results[0]
    for key in ("resource_id", "name", "description", "source_type", "snippet", "score", "retrieval_mode"):
        assert key in res, f"missing field frontend expects: {key}"
    assert res["retrieval_mode"] == "fulltext_like"
    assert isinstance(res["score"], float)


def test_search_route_scope_user_filters(tmp_path, monkeypatch):
    """scope=user returns only user_provided knowledge, not seed/platform."""
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    try:
        _make_knowledge_body(db, tmp_path, "用户文档Scope", "# 用户贡献的内容。", source_type="user_provided")
        db.commit()
    finally:
        db.close()

    from app.main import app
    c = TestClient(app)
    user_resp = c.get("/api/knowledge/search?q=贡献&scope=user")
    platform_resp = c.get("/api/knowledge/search?q=贡献&scope=platform")
    assert user_resp.status_code == 200 and platform_resp.status_code == 200
    assert len(user_resp.json()["data"]["results"]) >= 1
    # platform scope must NOT return user_provided doc
    assert len(platform_resp.json()["data"]["results"]) == 0
