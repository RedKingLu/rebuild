"""R16-E1: community /docs API + seed contract.

GET /docs        -> list (CommunityDocListResponse: docs[], total)
GET /docs/{slug} -> detail (CommunityDocDetail incl body_markdown)
404 on unknown slug. list must not leak full body_markdown.

Mirrors the proven test_api.py fixture: env set via monkeypatch, then
`import app.main as main_mod` (db reads env at import). TestClient triggers
startup -> init_db() + seed_on_startup() against an isolated temp DB.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path_factory, monkeypatch):
    data = tmp_path_factory.mktemp("comm")
    monkeypatch.setenv("COMMUNITY_DATA_DIR", str(data))
    monkeypatch.setenv("COMMUNITY_DATABASE_URL", f"sqlite:///{data / 'community.db'}")
    import app.main as main_mod
    with TestClient(main_mod.app) as c:
        yield c


def test_docs_list_returns_seeded_docs(client):
    r = client.get("/docs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 3
    slugs = [d["slug"] for d in body["docs"]]
    assert "xinchuang-overview" in slugs
    # list items must NOT leak full body (summary only)
    assert "body_markdown" not in body["docs"][0]


def test_docs_detail_returns_markdown_body(client):
    r = client.get("/docs/xinchuang-overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "xinchuang-overview"
    assert body["title"] == "信创迁移概述"
    assert "body_markdown" in body
    assert "信创" in body["body_markdown"]
    assert body["category"] == "信创基础"


def test_docs_detail_404_for_unknown(client):
    r = client.get("/docs/no-such-doc")
    assert r.status_code == 404, r.text


def test_docs_list_category_filter(client):
    r = client.get("/docs?category=数据库迁移")
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(d["category"] == "数据库迁移" for d in body["docs"])
    assert body["total"] >= 1


def test_status_includes_doc_count(client):
    r = client.get("/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "doc_count" in body
    assert body["doc_count"] >= 3


def test_docs_detail_contains_markdown_body_not_summary(client):
    """Detail exposes body_markdown distinct from summary (proves real doc body)."""
    r = client.get("/docs/dm-migration-guide")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["body_markdown"]) > len(body["summary"])
    assert "达梦" in body["body_markdown"]
