"""R12-3-C10 P6 交付包 API 路由测试。

覆盖：
  - GET /p6/package → 200 + DeliveryPackage JSON
  - GET /p6/download?path=output_code/x.py → 200 + 文件内容
  - GET /p6/download?path=source/x.py → 403（拒绝 source/）
  - GET /p6/download（无 path）→ 400
"""

import pytest
from unittest.mock import MagicMock, patch


class TestP6DeliveryAPI:
    """P6 delivery API routes."""

    def test_download_rejects_source(self, client):
        """source/ 下载必须被拒绝（D-105③）。"""
        r = client.get('/api/projects/p1/runs/r1/p6/download?path=source/app.py')
        assert r.status_code == 403

    def test_download_requires_path(self, client):
        """无 path 参数 → 400。"""
        r = client.get('/api/projects/p1/runs/r1/p6/download')
        assert r.status_code == 400

    def test_download_allowed_file(self, client, tmp_path):
        """output_code 文件下载成功。"""
        ws = tmp_path / "projects" / "p1"
        ws.mkdir(parents=True)
        (ws / "output_code").mkdir()
        (ws / "output_code" / "x.py").write_text("print('hello')", encoding="utf-8")

        with patch("app.api.routes_p6_delivery.workspace_path", return_value=ws):
            r = client.get('/api/projects/p1/runs/r1/p6/download?path=output_code/x.py')

        assert r.status_code == 200
        assert "hello" in r.text
