"""R15-4-C5 regression tests: Agent auto-introduce community resource → one Gate.

Locks the contract (without needing the live community service):
  - introduce() finds a candidate → creates ONE community_resource_introduction Gate
  - re-introduce of an already-imported+enabled resource → "available", NO second gate
  - community unavailable / no candidate → honest status, never a fabricated gate
  - import_resource() verifies sha256; mismatch → ChecksumMismatchError (P0)
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.database import get_session
from app.models.resource_entry import SourceType
from app.services.community_introduction import introduce, import_resource


# NOTE: real community API uses `id` (not `resource_id`) — connector maps c.get("id").
_RESOURCE = {
    "id": "community:case-1",
    "name": "case-1",
    "display_name": "Oracle→达梦 迁移案例",
    "resource_type": "case",
    "description": "案例",
    "tags": ["信创"], "categories": ["案例"], "license": "Apache-2.0",
    "source": "community", "verified": True, "download_count": 10,
    "checksum_sha256": "abc123",
}
_DETAIL = {"name": "case-1", "resource_type": "case", "description": "d",
           "version": "1.0.0", "tags": [], "categories": [], "download_count": 10,
           "icon_url": None, "manifest": None}


class _Resp:
    """Minimal httpx.Response stand-in."""
    def __init__(self, payload: dict | bytes, headers: dict | None = None, is_json: bool = True):
        self._payload = payload
        self.headers = headers or {}
        self.is_json = is_json
    def json(self):
        if not self.is_json:
            raise ValueError("not json")
        return self._payload
    @property
    def content(self):
        return self._payload if isinstance(self._payload, bytes) else str(self._payload).encode()
    def raise_for_status(self): pass


class _FakeClient:
    """Context-manager httpx.Client stand-in routing by path."""
    def __init__(self, base_url="", **kw): self.base_url = base_url
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, path, params=None):
        if path.endswith("/download"):
            payload = b"real package bytes"
            sha = hashlib.sha256(payload).hexdigest()
            return _Resp(payload, headers={"X-Checksum-SHA256": sha}, is_json=False)
        if path.endswith("/manifest"):
            return _Resp({"resource_id": "community:case-1", "version": "1.0.0",
                          "type": "case", "files": [], "checksum_sha256": "abc123", "dependencies": []})
        if path.startswith("/resources/"):
            return _Resp(_DETAIL)
        if path == "/resources":
            return _Resp({"totalSize": 1, "offset": 0, "resources": [_RESOURCE]})
        if path == "/status":
            return _Resp({"status": "ok", "version": "x", "resource_count": 1,
                          "model_count": 0, "evaluation_count": 0})
        return _Resp({})


@pytest.fixture()
def db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()


def test_introduce_creates_one_gate(db):
    with patch("app.services.community_connector._client", _FakeClient), \
         patch("app.services.community_introduction.settings") as s:
        s.community_base_url_normalized = "http://community"
        res = introduce(db, query="达梦", type="case", project_id="p1", stage="p4")
    assert res["status"] == "awaiting_approval"
    assert res["gate_type"] == "community_resource_introduction"
    assert res["candidate"]["resource_id"] == "community:case-1"
    # gate genuinely persisted
    from app.dependencies import get_services
    gates = get_services().gate_service.list_by_project("p1")
    intro = [g for g in gates if g.gate_type == "community_resource_introduction"]
    assert len(intro) == 1
    assert intro[0].gate_status == "waiting_decision"


def test_reintroduce_already_enabled_returns_available_no_second_gate(db):
    # seed a local enabled community resource for community:case-1
    from app.models.resource_entry import ResourceEntry, ResourceStatus, TrustLevel, RiskLevel
    entry = ResourceEntry(name="case-1", resource_type="case", source_type=SourceType.community,
                          source_path_or_ref="community:case-1", source_trust_level=TrustLevel.read_only_reference,
                          status=ResourceStatus.active, enabled=True)
    db.add(entry); db.commit()
    with patch("app.services.community_connector._client", _FakeClient), \
         patch("app.services.community_introduction.settings") as s:
        s.community_base_url_normalized = "http://community"
        res = introduce(db, query="达梦", type="case", project_id="p2", stage="p4")
    assert res["status"] == "available"
    assert res["source"] == "local_community_cache"
    # NO second gate created
    from app.dependencies import get_services
    gates = get_services().gate_service.list_by_project("p2")
    assert not any(g.gate_type == "community_resource_introduction" for g in gates)


def test_no_candidate_returns_honest_status(db):
    class _Empty(_FakeClient):
        def get(self, path, params=None):
            if path == "/resources":
                return _Resp({"totalSize": 0, "offset": 0, "resources": []})
            return super().get(path, params)
    with patch("app.services.community_connector._client", _Empty), \
         patch("app.services.community_introduction.settings") as s:
        s.community_base_url_normalized = "http://community"
        res = introduce(db, query="不存在的资源xyz", project_id="p3", stage="p4")
    assert res["status"] == "no_candidate"
    from app.dependencies import get_services
    assert not get_services().gate_service.list_by_project("p3")


def test_community_unreachable_no_unfulfillable_gate(db):
    # community_available=False (static fallback only) must NOT yield an unfulfillable gate.
    # Patch the provider at the source introduce() calls.
    with patch("app.services.online_source_provider.search_community_resources",
               return_value={"items": [{"resource_id": "x", "name": "x",
                                        "resource_type": "knowledge",
                                        "online_source": "static_R15_fallback"}],
                               "community_available": False, "total": 1}):
        res = introduce(db, query="x", project_id="p4", stage="p4")
    assert res["status"] == "community_unreachable"
    from app.dependencies import get_services
    assert not get_services().gate_service.list_by_project("p4")


def test_import_sha256_mismatch_rejects(db):
    # download whose header sha does NOT match the bytes (P0)
    class _Bad(_FakeClient):
        def get(self, path, params=None):
            if path.endswith("/download"):
                return _Resp(b"real bytes", headers={"X-Checksum-SHA256": "0" * 64}, is_json=False)
            return super().get(path, params)
    with patch("app.services.community_connector._client", _Bad), \
         patch("app.services.community_introduction.settings") as s:
        s.community_base_url_normalized = "http://community"
        from app.services.community_connector import ChecksumMismatchError
        with pytest.raises(ChecksumMismatchError):
            import_resource(db, "community:case-1")
