"""R17.5-P6-R2 (GAP-P6-4, capability-first) — P6 交付能力接线 + 诚实降级测试（对称 P5-R2）。

覆盖（无真实目标信创运行时 / 无外部签名设施 / 本轮维持逐文件下载，全部确定性 + mock 环境探测）：
  (a) 能力清单暴露：known_capabilities 暴露四类交付能力；probe_all 产能力矩阵（非门禁事实）。
  (b) 能力被探测产事实：各能力按【真实环境探测】返回 status + probe 明细。
  (c) 环境/设施缺失 → 诚实降级：无目标运行时/无网络/无 gpg → evidence_gap / not_applicable +
      capability_ready，"待需要时/环境启用"，非阻断、绝不伪造"通过/available/已交付"。
      未知能力 / 探测异常同样诚实降级不崩。
  (d) 新能力不翻转任何门禁：能力分区是【非门禁】事实——handler 注入能力探测后，
      P6 status（completed/blocked）+ 双向门禁 + final gate 仍由确定性交付内核认定，不受能力影响。
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from app.services.p6_capability_service import (
    P6CapabilityService, DeliveryCapability,
    CAP_AVAILABLE, CAP_EVIDENCE_GAP, CAP_NOT_APPLICABLE,
)
from app.graph.stage_handlers import RealP6Handler
from app.services.p5_input_service import P4InputFacts
from app.services.p6_delivery_service import DeliveryPackage


# ── (a) 能力清单暴露 ─────────────────────────────────────────────────────────

class TestDeliveryCapabilityInventory:

    def test_known_capabilities_exposed(self):
        svc = P6CapabilityService()
        caps = svc.known_capabilities()
        for c in ("physical_packaging", "deployment_smoke",
                  "license_network_verification", "package_integrity_external"):
            assert c in caps

    def test_probe_all_returns_matrix_non_gate(self):
        svc = P6CapabilityService()
        # 无任何环境/设施 → 诚实降级
        with patch.object(svc, "_has_zip_capability", return_value=False), \
             patch.object(svc, "_has_network_access", return_value=False), \
             patch.object(svc, "_has_gpg", return_value=None), \
             patch.object(svc, "_remote_host_id", return_value=None):
            out = svc.probe_all("proj")
        assert "capabilities" in out and isinstance(out["capabilities"], list)
        assert out["summary"]["total"] == len(svc.known_capabilities())
        # 非门禁事实：不含 can_be_completed / pass / completed 结论字段
        assert "can_be_completed" not in out
        assert "completed" not in out
        # 环境全缺 → 无一 available
        assert out["summary"]["environment_available"] == 0
        assert out["summary"]["evidence_gap"] + out["summary"]["not_applicable"] == out["summary"]["total"]


# ── (b) 能力被探测产事实（真实环境探测，mock 底层探测点）─────────────────────

class TestCapabilityProducesFacts:

    def test_packaging_probe_records_zip_fact(self):
        svc = P6CapabilityService()
        with patch.object(svc, "_has_zip_capability", return_value=True):
            cap = svc.probe_physical_packaging()
        assert cap.probe["zipfile_available"] is True
        assert cap.environment_available is True
        # Q-P6-3 维持逐文件下载 → 反伪造：即便 zipfile 就位，封装设施未启用 → evidence_gap
        assert cap.status == CAP_EVIDENCE_GAP

    def test_deployment_smoke_evidence_gap_when_host_bound(self):
        svc = P6CapabilityService()
        with patch.object(svc, "_remote_host_id", return_value="host-1"):
            cap = svc.probe_deployment_smoke("proj")
        assert cap.probe["remote_host_bound"] is True
        assert cap.environment_available is True
        # 有目标运行时绑定但本轮不实跑冒烟 → evidence_gap（不臆测已部署通过）
        assert cap.status == CAP_EVIDENCE_GAP

    def test_license_network_probe_records_network_fact(self):
        svc = P6CapabilityService()
        with patch.object(svc, "_has_network_access", return_value=True):
            cap = svc.probe_license_network_verification()
        assert cap.probe["network_reachable"] is True
        # 反伪造：网络仅前置条件，核验设施未启用（留后续轮次）→ evidence_gap，不标已核实
        assert cap.status == CAP_EVIDENCE_GAP

    def test_integrity_probe_records_gpg_fact(self):
        svc = P6CapabilityService()
        with patch.object(svc, "_has_gpg", return_value="/usr/bin/gpg"):
            cap = svc.probe_package_integrity_external()
        assert cap.probe["gpg_binary"] is True
        assert cap.environment_available is True
        # 反伪造：外部签名设施未启用 → evidence_gap（内部 SHA-256 由确定性内核产出，非本能力）
        assert cap.status == CAP_EVIDENCE_GAP


# ── (c) 环境/设施缺失 → 诚实降级，非阻断，绝不伪造 ───────────────────────────

class TestHonestDegradeNoFabrication:

    def test_deployment_smoke_not_applicable_without_target_env(self):
        svc = P6CapabilityService()
        with patch.object(svc, "_remote_host_id", return_value=None):
            cap = svc.probe_deployment_smoke("proj")
        assert cap.status == CAP_NOT_APPLICABLE
        assert cap.environment_available is False
        assert cap.capability_ready is True
        # 反伪造：绝不臆测已部署验证通过
        assert "臆测" in cap.note or "不" in cap.note
        assert cap.status != CAP_AVAILABLE

    def test_all_capabilities_degrade_without_env(self):
        svc = P6CapabilityService()
        with patch.object(svc, "_has_zip_capability", return_value=False), \
             patch.object(svc, "_has_network_access", return_value=False), \
             patch.object(svc, "_has_gpg", return_value=None), \
             patch.object(svc, "_remote_host_id", return_value=None):
            for c in svc.known_capabilities():
                cap = svc.verify_capability("proj", c)
                assert cap.status in (CAP_EVIDENCE_GAP, CAP_NOT_APPLICABLE), c
                assert cap.status != CAP_AVAILABLE, c
                d = cap.to_dict()
                assert d["environment_available"] is False, c

    def test_unknown_capability_honest_not_crash(self):
        svc = P6CapabilityService()
        cap = svc.verify_capability("proj", "no_such_capability")
        assert cap.status == CAP_EVIDENCE_GAP
        assert cap.capability_wired is False
        assert cap.capability_ready is False
        assert "known_capabilities" in cap.probe

    def test_probe_exception_degrades_gracefully(self):
        svc = P6CapabilityService()
        with patch.object(svc, "probe_deployment_smoke", side_effect=RuntimeError("boom")):
            cap = svc.verify_capability("proj", "deployment_smoke")
        assert cap.status == CAP_EVIDENCE_GAP           # 诚实降级，不崩
        assert "probe_error" in cap.probe

    def test_network_probe_failure_is_non_blocking(self):
        svc = P6CapabilityService()
        with patch("app.services.p6_capability_service.socket.create_connection",
                   side_effect=OSError("no network")):
            # _has_network_access 吞异常返回 False → 能力 evidence_gap，不抛
            assert svc._has_network_access() is False

    def test_no_capability_is_available_in_bare_sandbox(self):
        """本沙箱四类交付能力均待环境/设施启用——绝不伪造 available。"""
        svc = P6CapabilityService()
        with patch.object(svc, "_has_zip_capability", return_value=True), \
             patch.object(svc, "_has_network_access", return_value=True), \
             patch.object(svc, "_has_gpg", return_value="/usr/bin/gpg"), \
             patch.object(svc, "_remote_host_id", return_value=None):
            out = svc.probe_all("proj")
        # 即便工具就位（zip/网络/gpg），设施本轮未启用 → 无一 available（反伪造）
        assert out["summary"]["environment_available"] == 0


# ── (d) 新能力不翻转任何门禁（非门禁）────────────────────────────────────────

class _AllGapCapabilityService:
    """探测所有交付能力都 evidence_gap/not_applicable（模拟本地无环境）——绝不产 available。"""
    def probe_all(self, project_id):
        return {"note": "all gap", "capabilities": [
            {"capability": "physical_packaging", "status": CAP_EVIDENCE_GAP,
             "capability_ready": True, "environment_available": False}],
            "summary": {"total": 1, "environment_available": 0,
                        "evidence_gap": 1, "not_applicable": 0}}


def _completed_pkg():
    pkg = DeliveryPackage(project_id="p", run_id="r")
    pkg.delivery_manifest = {"contents": {"output_code_count": 1, "patch_count": 0}}
    pkg.risk_manifest = {"risk_count": 0, "has_blocking": False, "risks": []}
    pkg.hash_manifest = {"files": [{"path": "output_code/x.py", "sha256": "abc"}]}
    pkg.p6_delivery_report = {"summary": {}}
    pkg.p5_validation_report = {"evidence_refs": ["ev-1"]}
    pkg.indexes = {"artifact_index": [], "evidence_index": [],
                   "trace_index": [], "audit_index": []}
    pkg.desensitization_ok = True
    pkg.desensitization_issues = []
    return pkg


class TestCapabilityCannotFlipGate:

    def test_all_gap_capability_does_not_flip_completed(self):
        """交付内核 completed → 能力全 evidence_gap/not_applicable 不改它；能力分区随出为非门禁事实。"""
        handler = RealP6Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p6_capability_service=_AllGapCapabilityService())
        p4_input = P4InputFacts(
            project_id="p", run_id="r", blocked=False,
            p4_to_p5_gate_status="approved",
            output_code_refs=["output_code/x.py"], evidence_refs=["ev-1"])
        mock_input_svc = MagicMock()
        mock_input_svc.read_p4_input.return_value = p4_input
        mock_p6_svc = MagicMock()
        mock_p6_svc.generate_delivery_package.return_value = _completed_pkg()
        handler._p6_svc = mock_p6_svc

        mock_gate_resp = MagicMock()
        mock_gate_resp.gate_id = "gate-p6-final"
        mock_svc = MagicMock()
        mock_svc.gate_service.create.return_value = mock_gate_resp

        with patch.object(handler, "_services", return_value=mock_svc), \
             patch.object(RealP6Handler, "_load_p5_report",
                          return_value={"can_be_completed": True,
                                        "validation_plan": {"slots": []}}), \
             patch("app.services.p5_input_service.P5InputService") as mock_cls:
            mock_cls.return_value = mock_input_svc
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        # 门禁由确定性交付内核认定 completed，能力分区未参与
        assert result["status"] == "completed"
        assert result["p6_final_gate_id"] == "gate-p6-final"
        # 能力分区作【非门禁】事实带出
        assert "delivery_capabilities" in result
        assert result["delivery_capabilities"]["summary"]["evidence_gap"] == 1

    def test_capability_cannot_rescue_p5_not_passed(self):
        """P5 未通过 → 即便能力探测本可跑，P6 仍 blocked、不产能力分区（早于内核返回）。"""
        handler = RealP6Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p6_capability_service=_AllGapCapabilityService())
        mock_input_svc = MagicMock()
        mock_input_svc.read_p4_input.return_value = P4InputFacts(
            project_id="p", run_id="r", blocked=False, p4_to_p5_gate_status="approved",
            output_code_refs=["output_code/x.py"], evidence_refs=["ev-1"])
        handler._p6_svc = MagicMock()

        with patch.object(RealP6Handler, "_load_p5_report",
                          return_value={"can_be_completed": False,
                                        "blocked_reason": "hard slot failed"}), \
             patch("app.services.p5_input_service.P5InputService") as mock_cls:
            mock_cls.return_value = mock_input_svc
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        assert result["status"] == "blocked"
        assert "P5" in result["reason"]
