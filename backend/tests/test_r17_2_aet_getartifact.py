"""R17.2 OD-11 回归测试：AETService.get_artifact 真实文件解析。

修复前 get_artifact 恒返 None（门面），GET /artifacts/{id} 永远 404。
本测试真实写入 artifacts 目录下的文件，断言 get_artifact 返回真实元数据
（content_hash == 文件真实 sha256、bytes 正确、source_status == "real"），
并断言未命中 id 返回 None。不打桩文件读取，只 patch workspace_path 指向临时目录。
"""

import hashlib
from unittest.mock import patch

from app.services.aet_service import AETService


def _make_service():
    # get_artifact 不使用 self._svc，传 None 即可（真实构造签名 AETService(services)）。
    return AETService(None)


def test_get_artifact_returns_real_metadata(tmp_path):
    """写入真实文件 → get_artifact 返回真实 dict，content_hash 等于真实 sha256。"""
    pid = "proj-r17-2-aet"
    ws = tmp_path / "projects" / pid
    art_dir = ws / "artifacts"
    art_dir.mkdir(parents=True)
    # stem 故意含 '-'，验证 "artifact-" 前缀剥离而非在首个 '-' 处切断。
    content = b"# migration-report\nreal artifact bytes\n"
    f = art_dir / "migration-report.md"
    f.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    svc = _make_service()
    with patch("app.services.aet_service.workspace_path", return_value=ws):
        result = svc.get_artifact("artifact-migration-report", project_id=pid)

    assert result is not None, "真实存在的 artifact 不应返回 None"
    assert result["content_hash"] == expected_hash
    assert result["content_hash"] != ""
    assert result["bytes"] == len(content)
    assert result["source_status"] == "real"
    assert result["mock_level"] == "real"
    assert result["name"] == "migration-report.md"
    assert result["path"] == "artifacts/migration-report.md"
    assert result["artifact_id"] == "artifact-migration-report"
    assert result["artifact_type"] == "md"


def test_get_artifact_unknown_id_returns_none(tmp_path):
    """不存在的 artifact_id → None（route 保持 404）。"""
    pid = "proj-r17-2-aet"
    ws = tmp_path / "projects" / pid
    (ws / "artifacts").mkdir(parents=True)

    svc = _make_service()
    with patch("app.services.aet_service.workspace_path", return_value=ws):
        assert svc.get_artifact("artifact-does-not-exist", project_id=pid) is None


def test_get_artifact_no_project_id_returns_none(tmp_path):
    """缺 project_id → None（不猜测、不扫描）。"""
    svc = _make_service()
    assert svc.get_artifact("artifact-x", project_id=None) is None


def test_get_artifact_malformed_id_returns_none(tmp_path):
    """id 不带 'artifact-' 前缀 → None。"""
    pid = "proj-r17-2-aet"
    ws = tmp_path / "projects" / pid
    art_dir = ws / "artifacts"
    art_dir.mkdir(parents=True)
    (art_dir / "foo.txt").write_bytes(b"x")

    svc = _make_service()
    with patch("app.services.aet_service.workspace_path", return_value=ws):
        assert svc.get_artifact("foo", project_id=pid) is None


def test_get_artifact_consistent_with_list(tmp_path):
    """get_artifact 命中的 id 与 list_artifacts 输出的 artifact_id 一致。"""
    pid = "proj-r17-2-aet"
    ws = tmp_path / "projects" / pid
    art_dir = ws / "artifacts"
    art_dir.mkdir(parents=True)
    (art_dir / "plan_v1.json").write_bytes(b'{"k": 1}')

    svc = _make_service()
    with patch("app.services.aet_service.workspace_path", return_value=ws):
        listed = svc.list_artifacts(project_id=pid)
        assert len(listed) == 1
        aid = listed[0]["artifact_id"]
        detail = svc.get_artifact(aid, project_id=pid)
    assert detail is not None
    assert detail["artifact_id"] == aid
    assert detail["name"] == "plan_v1.json"
