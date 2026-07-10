"""R15-4-C7: knowledge-package zip importer + content endpoint."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


def _make_knowledge_zip(docs: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    manifest = {
        "name": "test-basics", "version": "1.0.0", "type": "knowledge",
        "source": "official", "description": "测试知识包",
        "tags": ["测试"], "categories": ["知识"], "files": list(docs.keys()),
    }
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, content in docs.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.fixture()
def client_and_db(tmp_path, monkeypatch):
    import app.core.config as cfg
    import app.core.database as db_mod
    from app.dependencies import clear_services_cache
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "data"))
    object.__setattr__(cfg.settings, "source_dir", str(tmp_path / "source"))
    object.__setattr__(cfg.settings, "database_url", f"sqlite:///{tmp_path / 'rebuild.db'}")
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()
    from app.main import app
    return TestClient(app)


def test_import_knowledge_package_creates_entries(client_and_db):
    client = client_and_db
    docs = {"what-is.md": "# 什么是 rebuild\n\n正文内容。", "quickstart.md": "# 快速入门\n\n步骤。"}
    data = _make_knowledge_zip(docs)
    resp = client.post("/api/knowledge/import-package",
                       files={"file": ("pkg.zip", data, "application/zip")},
                       data={"scope": "platform"})
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["imported_count"] == 2
    assert body["package"] == "test-basics"
    ids = [r["resource_id"] for r in body["resources"]]
    assert len(ids) == 2


def test_content_endpoint_returns_markdown_body(client_and_db):
    client = client_and_db
    docs = {"what-is.md": "# 什么是 rebuild\n\n这是正文。"}
    data = _make_knowledge_zip(docs)
    imp = client.post("/api/knowledge/import-package",
                      files={"file": ("pkg.zip", data, "application/zip")},
                      data={"scope": "platform"}).json()
    rid = imp["data"]["resources"][0]["resource_id"]
    resp = client.get(f"/api/resources/{rid}/content")
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["content"] is not None, "expected a Markdown body"
    assert "这是正文" in (body["content"] or "")
    assert body["content_type"] == "text/markdown"


def test_import_rejects_non_zip(client_and_db):
    client = client_and_db
    resp = client.post("/api/knowledge/import-package",
                       files={"file": ("pkg.md", b"not a zip", "text/markdown")})
    assert resp.status_code == 422


def test_docs_route_has_no_backend_endpoint(client_and_db):
    """R15-4-C7: backend has no dedicated /docs endpoint (frontend route was removed
    separately). An unregistered backend path returns 404 (not a 200 SPA shell)."""
    client = client_and_db
    r = client.get("/api/docs", follow_redirects=False)
    assert r.status_code == 404, f"/api/docs returned {r.status_code}, expected 404"
