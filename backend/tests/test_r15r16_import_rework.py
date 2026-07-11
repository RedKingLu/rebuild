"""R15+R16 返工验收测试 — URL/文件导入路径加固（P0-3 / P1-3 / A 方案 sha256）。

覆盖：
- 导入即落 status=active + enabled=True（D-061 修订，P0-3 根因：draft 不可调度）
- 不写审核状态机业务值（review_status 保持中性 not_reviewed，不作为门禁）
- 导入的资源可被 resource_loader 调度（schedulable=True）
- A 方案尽力校验：响应含 X-Checksum-SHA256 头则比对包体 sha256，不匹配 → 409
- imported_version 从 manifest/JSON version 推断并落库（E3 追溯）
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile


def _make_zip(manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("body.md", "# imported resource body")
    return buf.getvalue()


def _patch_download(monkeypatch, raw: bytes, headers: dict) -> None:
    async def _fake(url):  # noqa: ANN001
        return raw, headers
    monkeypatch.setattr("app.api.routes_imports._download", _fake)


# ── P0-3: 导入即 active + enabled + 可调度 ────────────────────────────────

def test_import_resource_url_json_lands_active_enabled(client, monkeypatch):
    body = json.dumps({"resource_type": "tool", "name": "URL Tool", "version": "2.1.0"}).encode()
    _patch_download(monkeypatch, body, {})
    resp = client.post("/api/import/resource", data={"community_url": "https://example.com/r.json"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "active"
    assert data["enabled"] is True
    assert data["source_type"] == "community"
    # 不再写审核状态机业务值（deprecated 列取中性默认）
    assert data["review_status"] == "not_reviewed"
    # E3 追溯版本
    assert data["imported_version"] == "2.1.0"


def test_imported_resource_is_schedulable(client, monkeypatch):
    """P0-3 根因回归：导入的资源必须能被 resource_loader 调度（draft 不可调度 bug 已修）。"""
    body = json.dumps({"resource_type": "tool", "name": "Schedulable Tool"}).encode()
    _patch_download(monkeypatch, body, {})
    resp = client.post("/api/import/resource", data={"community_url": "https://example.com/s.json"})
    assert resp.status_code == 200, resp.text
    rid = resp.json()["data"]["resource_id"]

    from app.core.database import get_session
    from app.services import resource_loader
    db = get_session()
    try:
        lr = resource_loader.load(rid, db)
        assert lr is not None
        assert lr.schedulable is True, "imported resource must be schedulable (no review gate, status=active)"
        assert "review_required" not in (lr.reason or "")
    finally:
        db.close()


# ── A 方案 sha256 尽力校验 ────────────────────────────────────────────────

def test_import_resource_url_checksum_mismatch_409(client, monkeypatch):
    raw = _make_zip({"name": "tampered", "resource_type": "tool"})
    _patch_download(monkeypatch, raw, {"X-Checksum-SHA256": "deadbeef_not_matching"})
    resp = client.post("/api/import/resource", data={"community_url": "https://example.com/r.zip"})
    assert resp.status_code == 409, resp.text


def test_import_resource_url_checksum_match_ok(client, monkeypatch):
    raw = _make_zip({"name": "ZipMatch", "resource_type": "tool", "version": "1.2.3"})
    good = hashlib.sha256(raw).hexdigest()
    _patch_download(monkeypatch, raw, {"X-Checksum-SHA256": good})
    resp = client.post("/api/import/resource", data={"community_url": "https://example.com/r.zip"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "active" and data["enabled"] is True
    assert data["imported_version"] == "1.2.3"


def test_import_resource_url_no_header_best_effort_passes(client, monkeypatch):
    """无 X-Checksum-SHA256 头 → 记录并放行（短期不强制），不阻断导入。"""
    body = json.dumps({"resource_type": "knowledge", "name": "No Header"}).encode()
    _patch_download(monkeypatch, body, {})
    resp = client.post("/api/import/resource", data={"community_url": "https://example.com/n.json"})
    assert resp.status_code == 200, resp.text


# ── 文件上传路径同样落 active + enabled ───────────────────────────────────

def test_import_resource_file_lands_active_enabled(client):
    resp = client.post(
        "/api/import/resource",
        data={"name": "Uploaded Guide", "resource_type": "knowledge"},
        files={"file": ("guide.md", b"# Guide\n\ncontent", "text/markdown")},
    )
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()["data"]
    assert data["status"] == "active"
    assert data["enabled"] is True
    assert data["source_type"] == "user_provided"
    assert data["review_status"] == "not_reviewed"


# ── 案例导入同样 active + enabled + imported_version ──────────────────────

def test_import_case_url_lands_active_enabled(client, monkeypatch):
    body = json.dumps({"name": "Migration Case", "version": "0.9"}).encode()
    _patch_download(monkeypatch, body, {})
    resp = client.post("/api/import/case", data={"community_url": "https://example.com/c.json"})
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()["data"]
    assert data["resource_type"] == "case"
    assert data["status"] == "active" and data["enabled"] is True
    assert data["imported_version"] == "0.9"


# ── 自描述 manifest 还原资源真实身份（R15-R16 返工 E2E 回归）─────────────
# 社区包 manifest 含 resource_type/name/display_name/version 时，导入须还原真实身份，
# 且 display_name 优先于 name（避免落为默认 tool/"Imported Resource"）。

def test_import_resource_zip_manifest_preserves_identity(client, monkeypatch):
    manifest = {
        "resource_id": "case-x", "resource_type": "case",
        "name": "oracle-to-dm", "display_name": "Oracle → 达梦 迁移案例",
        "description": "迁移案例", "version": "1.2.0",
    }
    raw = _make_zip(manifest)
    _patch_download(monkeypatch, raw, {})
    resp = client.post("/api/import/resource", data={"community_url": "https://example.com/case-x.zip"})
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()["data"]
    assert data["resource_type"] == "case"            # 非默认 tool
    assert data["name"] == "Oracle → 达梦 迁移案例"     # display_name 优先
    assert data["imported_version"] == "1.2.0"
    assert data["status"] == "active" and data["enabled"] is True
