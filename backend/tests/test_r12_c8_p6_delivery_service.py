"""R12-3-C8 P6 交付包服务测试。

覆盖 P6DeliveryService.generate_delivery_package()：
  - 正常路径：完整交付包生成
  - output_code/patch 文件信息（sha256 / bytes）
  - risk_manifest 记录未通过项
  - hash_manifest 完整性
  - p5_validation_report + p6_delivery_report
  - 脱敏扫描（D-032）
  - P4 输入 blocked → 诚实 blocked
  - source 不包含
"""

import hashlib
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.p5_input_service import P4InputFacts
from app.services.p6_delivery_service import (
    P6DeliveryService, DeliveryPackage, delivery_package_to_dict,
)


@pytest.fixture
def ws_with_p4(tmp_path):
    ws = tmp_path / "projects" / "p1"
    for d in ["output_code", "patches", "artifacts", "evidence", "source"]:
        (ws / d).mkdir(parents=True)
    code = ws / "output_code" / "migrate.py"
    code.write_text("# migrated code\nprint('hello')", encoding="utf-8")
    patch_f = ws / "patches" / "tn-001.diff"
    patch_f.write_text("--- a/x.py\n+++ b/x.py\n-old\n+new", encoding="utf-8")
    source = ws / "source" / "original.py"
    source.write_text("# original", encoding="utf-8")
    return ws


@pytest.fixture
def p4_input_ok(ws_with_p4):
    return P4InputFacts(
        project_id="p1", run_id="r1",
        blocked=False,
        p4_to_p5_gate_id="gate-ok",
        p4_to_p5_gate_status="approved",
        output_code_refs=["output_code/migrate.py"],
        patch_refs=["patches/tn-001.diff"],
        evidence_refs=["ev-p4-tn-001"],
        p4_summary_ref="artifacts/p4/p4_execution_summary.json",
        p4_execution_summary={"stage": "p4", "graph_status": "completed",
                              "change_manifest": []},
    )


class TestP6Delivery:

    def test_generate_full_package(self, ws_with_p4, p4_input_ok):
        svc = P6DeliveryService()
        with patch("app.services.p6_delivery_service.workspace_path",
                   return_value=ws_with_p4), \
             patch("app.services.p6_delivery_service.P5InputService") as mock_cls:
            mock_svc = MagicMock()
            mock_svc.read_p4_input.return_value = p4_input_ok
            mock_cls.return_value = mock_svc
            pkg = svc.generate_delivery_package("p1", "r1", p5_plan={
                "slots": [
                    {"slot_id": "output_code_exists", "status": "validated"},
                    {"slot_id": "build_verified", "status": "validated"},
                    {"slot_id": "tests_pass", "status": "validation_failed",
                     "evidence_gap_reason": "测试失败"},
                ],
            })

        assert pkg.project_id == "p1"
        # delivery_manifest
        manifest = pkg.delivery_manifest
        assert manifest["contents"]["output_code_count"] == 1
        assert manifest["contents"]["patch_count"] == 1
        # hash_manifest → sha256
        hashes = pkg.hash_manifest["files"]
        assert len(hashes) == 2
        code_hash = next(h for h in hashes if h["path"] == "output_code/migrate.py")
        expected_sha = hashlib.sha256(b"# migrated code\nprint('hello')").hexdigest()
        assert code_hash["sha256"] == expected_sha

    def test_risk_manifest_records_failed_slots(self, ws_with_p4, p4_input_ok):
        svc = P6DeliveryService()
        with patch("app.services.p6_delivery_service.workspace_path",
                   return_value=ws_with_p4), \
             patch("app.services.p6_delivery_service.P5InputService") as mock_cls:
            mock_svc = MagicMock()
            mock_svc.read_p4_input.return_value = p4_input_ok
            mock_cls.return_value = mock_svc
            pkg = svc.generate_delivery_package("p1", "r1", p5_plan={
                "slots": [
                    {"slot_id": "tests_pass", "status": "validation_failed",
                     "evidence_gap_reason": "测试失败"},
                    {"slot_id": "build_verified", "status": "evidence_gap",
                     "evidence_gap_reason": "命令不可识别"},
                ],
            })

        risks = pkg.risk_manifest["risks"]
        failed_risks = [r for r in risks if r["type"] == "verification_failed"]
        assert len(failed_risks) >= 2
        # 测试失败是 blocking
        test_risk = next(r for r in failed_risks if r.get("slot_id") == "tests_pass")
        assert test_risk["blocking"] is True

    def test_p4_blocked_returns_error_risk_manifest(self, ws_with_p4):
        """P4 输入 blocked → 诚实 blocked + risk_manifest。"""
        svc = P6DeliveryService()
        blocked_input = P4InputFacts(project_id="p1", run_id="r1",
                                     blocked=True, blocked_reason="Gate 未通过")
        with patch("app.services.p6_delivery_service.workspace_path",
                   return_value=ws_with_p4), \
             patch("app.services.p6_delivery_service.P5InputService") as mock_cls:
            mock_svc = MagicMock()
            mock_svc.read_p4_input.return_value = blocked_input
            mock_cls.return_value = mock_svc
            pkg = svc.generate_delivery_package("p1", "r1")

        assert pkg.risk_manifest.get("blocking") is True
        assert "Gate" in pkg.risk_manifest.get("error", "")

    def test_desensitization_scan_detects_secrets(self, ws_with_p4, p4_input_ok):
        """脱敏扫描检测到密钥（D-032）。"""
        # 写入含密钥的文件
        secret_file = ws_with_p4 / "output_code" / "config.py"
        secret_file.write_text("api_key = 'sk-abcdef1234567890'", encoding="utf-8")
        p4_input_ok.output_code_refs.append("output_code/config.py")

        svc = P6DeliveryService()
        with patch("app.services.p6_delivery_service.workspace_path",
                   return_value=ws_with_p4), \
             patch("app.services.p6_delivery_service.P5InputService") as mock_cls:
            mock_svc = MagicMock()
            mock_svc.read_p4_input.return_value = p4_input_ok
            mock_cls.return_value = mock_svc
            pkg = svc.generate_delivery_package("p1", "r1", p5_plan={"slots": []})

        assert pkg.desensitization_ok is False
        assert len(pkg.desensitization_issues) > 0

    def test_source_not_included(self, ws_with_p4, p4_input_ok):
        """source/ 默认不包含在交付包中（D-105③）。"""
        svc = P6DeliveryService()
        with patch("app.services.p6_delivery_service.workspace_path",
                   return_value=ws_with_p4), \
             patch("app.services.p6_delivery_service.P5InputService") as mock_cls:
            mock_svc = MagicMock()
            mock_svc.read_p4_input.return_value = p4_input_ok
            mock_cls.return_value = mock_svc
            pkg = svc.generate_delivery_package("p1", "r1", p5_plan={"slots": []})

        assert pkg.source_included is False
        # delivery_manifest 中不引用 source/
        for entry in pkg.delivery_manifest.get("output_code", []):
            assert not entry.get("path", "").startswith("source/")

    def test_serialization(self, ws_with_p4, p4_input_ok):
        """delivery_package_to_dict 序列化完整。"""
        pkg = DeliveryPackage(project_id="p1", run_id="r1")
        pkg.delivery_manifest = {"test": True}
        pkg.hash_manifest = {"files": []}
        pkg.risk_manifest = {"risk_count": 0, "has_blocking": False}
        d = delivery_package_to_dict(pkg)
        assert d["project_id"] == "p1"
        assert "desensitization" in d
        assert d["source_included"] is False
