"""R17.5-P5-R2 (GAP-P5-1 + GAP-P5-3, capability-first) — P5 验证维度能力接线测试。

覆盖（无真实 SDK/DB/浏览器/远程主机，全部确定性 + mock 环境探测）：
  (a) agent 可选维度并调用能力：p5_dimension_capabilities / p5_verify_dimension 工具返回能力矩阵；
      P5CapabilityService.known_dimensions 暴露可选维度。
  (b) 能力被调用产事实：各维度探测按【真实环境探测】返回 available / evidence_gap + probe 明细。
  (c) 环境缺失 → 诚实降级：无 SDK/浏览器/远程主机 → evidence_gap + capability_ready=True +
      "待环境真验"，非阻断、绝不伪造"通过/pass"。未知维度/探测异常同样诚实降级不崩。
  (d) 新能力不翻转 can_be_completed：能力分区是【非门禁】事实——
      can_mark_completed 只读十槽位；能力状态词表与 P5SlotStatus 分离；handler 注入能力探测后
      can_be_completed 仍由确定性门禁认定（硬必需失败 → blocked，即便所有维度能力都 evidence_gap）。
  (e) GAP-P5-3 .NET SDK-aware 命令补全：无 dotnet SDK → 诚实 needs_user_input（不伪造 echo pass）；
      有 SDK → 填真实 dotnet build/test/format 命令。
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from app.services.p5_capability_service import (
    P5CapabilityService, DimensionCapability, CAP_AVAILABLE, CAP_EVIDENCE_GAP,
)
from app.services.p5_validation_plan import (
    P5SlotStatus, HARD_REQUIRED_SLOTS, CONDITIONAL_SLOTS, can_mark_completed,
)
from app.services.p5_verification_service import SlotVerificationResult
from app.graph.stage_handlers import RealP5Handler
from app.services.p5_input_service import P4InputFacts


# ── (a) agent 可选维度并调用能力 ────────────────────────────────────────────

class TestDimensionCapabilityTools:

    def test_known_dimensions_exposed(self):
        svc = P5CapabilityService()
        dims = svc.known_dimensions()
        # 参考轨/skill 要求的维度都已接线为可选能力
        for d in ("browser_qa", "eval_harness", "db_migration", "business_flow",
                  "regression_baseline", "benchmark", "dotnet_build"):
            assert d in dims

    def test_tool_p5_dimension_capabilities_returns_matrix(self):
        from app.services.tool_registry import execute_tool
        out = asyncio.run(execute_tool("p5_dimension_capabilities", {}, "proj",
                                       stage="p5", db=None))
        assert "dimensions" in out and isinstance(out["dimensions"], list)
        assert out["summary"]["total"] == len(P5CapabilityService().known_dimensions())
        # 非门禁事实：不含 can_be_completed / pass 结论字段
        assert "can_be_completed" not in out

    def test_tool_p5_verify_dimension_single(self):
        from app.services.tool_registry import execute_tool
        out = asyncio.run(execute_tool("p5_verify_dimension", {"dimension": "browser_qa"},
                                       "proj", stage="p5", db=None))
        assert out["dimension"] == "browser_qa"
        assert out["sub_skill"] == "P-browser-qa"
        assert out["status"] in (CAP_AVAILABLE, CAP_EVIDENCE_GAP)
        assert "capability_ready" in out

    def test_tool_registered_in_builtin_schemas(self):
        from app.services.tool_registry import _BUILTIN_SCHEMAS
        names = {s["function"]["name"] for s in _BUILTIN_SCHEMAS}
        assert "p5_dimension_capabilities" in names
        assert "p5_verify_dimension" in names


# ── (b) 能力被调用产事实（真实环境探测，mock 底层探测点）────────────────────

class TestCapabilityProducesFacts:

    def test_dotnet_available_when_sdk_present(self):
        svc = P5CapabilityService()
        with patch("app.services.p5_capability_service.shutil.which", return_value="/usr/bin/dotnet"):
            cap = svc.probe_dotnet_build()
        assert cap.environment_available is True
        assert cap.status == CAP_AVAILABLE
        assert cap.probe["dotnet_sdk"] == "present"

    def test_browser_available_when_playwright_and_binary_present(self):
        svc = P5CapabilityService()
        with patch("app.services.p5_capability_service.importlib.util.find_spec",
                   return_value=object()), \
             patch("app.services.p5_capability_service.shutil.which", return_value="/usr/bin/chromium"):
            cap = svc.probe_browser_qa()
        assert cap.environment_available is True
        assert cap.status == CAP_AVAILABLE
        assert cap.probe["playwright_installed"] is True
        assert cap.probe["browser_binary"] is not None

    def test_eval_harness_available_when_remote_host_bound(self):
        svc = P5CapabilityService()
        with patch("app.services.workspace_service.resolve_default_remote_host_id",
                   return_value="host-1"):
            cap = svc.probe_eval_harness("proj")
        assert cap.environment_available is True
        assert cap.status == CAP_AVAILABLE
        assert cap.probe["remote_host_bound"] is True

    def test_regression_available_when_baseline_and_host(self):
        svc = P5CapabilityService()
        with patch.object(svc, "_p1_baseline_present",
                          return_value={"present": True, "ref": "artifacts/p1/acceptance_baseline.json"}), \
             patch.object(svc, "_remote_host_id", return_value="host-1"):
            cap = svc.probe_regression_baseline("proj")
        assert cap.environment_available is True
        assert cap.status == CAP_AVAILABLE
        assert cap.probe["p1_baseline_present"] is True

    def test_probe_all_summary_counts(self):
        svc = P5CapabilityService()
        # 无任何环境 → 全 evidence_gap
        with patch.object(svc, "_has_dotnet_sdk", return_value=None), \
             patch.object(svc, "_has_browser_automation",
                          return_value={"playwright_installed": False, "browser_binary": None}), \
             patch.object(svc, "_remote_host_id", return_value=None), \
             patch.object(svc, "_p1_baseline_present", return_value={"present": False, "ref": ""}):
            out = svc.probe_all("proj")
        assert out["summary"]["total"] == len(svc.known_dimensions())
        assert out["summary"]["evidence_gap"] == out["summary"]["total"]
        assert out["summary"]["environment_available"] == 0


# ── (c) 环境缺失 → 诚实降级，非阻断，绝不伪造 ────────────────────────────────

class TestHonestDegradeNoFabrication:

    def test_dotnet_absent_is_evidence_gap_capability_ready(self):
        svc = P5CapabilityService()
        with patch("app.services.p5_capability_service.shutil.which", return_value=None):
            cap = svc.probe_dotnet_build()
        assert cap.status == CAP_EVIDENCE_GAP
        assert cap.environment_available is False
        assert cap.capability_ready is True          # 能力已接线
        assert "待" in cap.note and "环境真验" in cap.note
        # 反伪造：绝不产"通过/pass"
        assert "pass" not in cap.status
        assert cap.status != "available"

    def test_all_dimensions_degrade_without_env(self):
        svc = P5CapabilityService()
        with patch.object(svc, "_has_dotnet_sdk", return_value=None), \
             patch.object(svc, "_has_browser_automation",
                          return_value={"playwright_installed": False, "browser_binary": None}), \
             patch.object(svc, "_remote_host_id", return_value=None), \
             patch.object(svc, "_p1_baseline_present", return_value={"present": False, "ref": ""}):
            for dim in svc.known_dimensions():
                cap = svc.verify_dimension("proj", dim)
                assert cap.status == CAP_EVIDENCE_GAP, dim
                assert cap.capability_ready is True, dim
                # 无任何"通过/完成"结论字样冒充
                d = cap.to_dict()
                assert d["environment_available"] is False

    def test_unknown_dimension_honest_not_crash(self):
        svc = P5CapabilityService()
        cap = svc.verify_dimension("proj", "no_such_dimension")
        assert cap.status == CAP_EVIDENCE_GAP
        assert cap.capability_wired is False
        assert cap.capability_ready is False
        assert "known_dimensions" in cap.probe

    def test_probe_exception_degrades_gracefully(self):
        svc = P5CapabilityService()
        with patch.object(svc, "probe_browser_qa", side_effect=RuntimeError("boom")):
            cap = svc.verify_dimension("proj", "browser_qa")
        assert cap.status == CAP_EVIDENCE_GAP           # 诚实降级，不崩
        assert "probe_error" in cap.probe

    def test_remote_resolve_failure_is_non_blocking(self):
        svc = P5CapabilityService()
        with patch("app.services.workspace_service.resolve_default_remote_host_id",
                   side_effect=RuntimeError("db down")):
            # _remote_host_id 吞异常返回 None → 维度 evidence_gap，不抛
            assert svc._remote_host_id("proj") is None


# ── (d) 新能力不翻转 can_be_completed（非门禁）──────────────────────────────

class _AllGapCapabilityService:
    """探测所有维度都 evidence_gap（模拟本地无环境）——绝不产 available/pass。"""
    def probe_all(self, project_id):
        return {"note": "all gap", "dimensions": [
            {"dimension": "browser_qa", "status": CAP_EVIDENCE_GAP,
             "capability_ready": True, "environment_available": False}],
            "summary": {"total": 1, "environment_available": 0, "evidence_gap": 1}}


def _base_input():
    return P4InputFacts(
        project_id="p", run_id="r", blocked=False,
        p4_to_p5_gate_id="gate-ok", p4_to_p5_gate_status="approved",
        output_code_refs=["output_code/x.cs"], patch_refs=["patches/tn-1.diff"],
        evidence_refs=["ev-1"], p4_execution_summary={"stage": "p4", "graph_status": "completed"})


class TestCapabilityCannotFlipGate:

    def test_capability_status_words_separate_from_slot_status(self):
        """能力状态词表与十槽位 P5SlotStatus 分离——能力不是槽位、不进门禁。"""
        slot_values = {s.value for s in P5SlotStatus}
        assert CAP_AVAILABLE not in slot_values or CAP_AVAILABLE == "available"
        # 关键：can_mark_completed 只读 plan.slots，不读任何 dimension_capabilities
        import inspect
        sig = inspect.signature(can_mark_completed)
        assert list(sig.parameters.keys()) == ["plan"]

    def test_all_gap_capability_does_not_flip_completed(self, tmp_path):
        """硬必需全过 → can_be_completed 由确定性门禁认定 True；能力全 evidence_gap 不改它。"""
        ws = tmp_path / "projects" / "p"
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)
        handler = RealP5Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p5_capability_service=_AllGapCapabilityService())
        mock_input = MagicMock()
        mock_input.read_p4_input.return_value = _base_input()
        handler._p5_input = mock_input
        mock_verify = MagicMock()
        mock_verify.verify_all_hard_required.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=True, status=P5SlotStatus.VALIDATED)
            for sid in HARD_REQUIRED_SLOTS]
        mock_verify.verify_conditional_slots.return_value = [
            SlotVerificationResult(slot_id=sid.value, passed=False, status=P5SlotStatus.NOT_APPLICABLE)
            for sid in CONDITIONAL_SLOTS]
        handler._p5_verify = mock_verify

        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        # can_be_completed 由确定性门禁认定，能力分区未参与
        assert result["validation_plan"]["can_be_completed"] is True
        assert result["status"] == "completed"
        # 能力分区落盘为【非门禁】事实
        report = json.loads((ws / "artifacts" / "p5_validation_report.json").read_text("utf-8"))
        assert "dimension_capabilities" in report
        assert report["dimension_capabilities"]["summary"]["evidence_gap"] == 1
        assert report["can_be_completed"] is True     # 由门禁，非能力分区

    def test_capability_cannot_rescue_failed_hard_slot(self, tmp_path):
        """硬必需失败 → 即便能力探测跑完，can_be_completed 仍 False、status=blocked。"""
        ws = tmp_path / "projects" / "p"
        (ws / "artifacts").mkdir(parents=True, exist_ok=True)
        handler = RealP5Handler(tracer=MagicMock(), auditor=MagicMock(),
                                p5_capability_service=_AllGapCapabilityService())
        mock_input = MagicMock()
        mock_input.read_p4_input.return_value = _base_input()
        handler._p5_input = mock_input
        mock_verify = MagicMock()
        hard = list(HARD_REQUIRED_SLOTS)
        mock_verify.verify_all_hard_required.return_value = [
            SlotVerificationResult(
                slot_id=sid.value, passed=(i != 0),
                status=(P5SlotStatus.VALIDATION_FAILED if i == 0 else P5SlotStatus.VALIDATED))
            for i, sid in enumerate(hard)]
        mock_verify.verify_conditional_slots.return_value = []
        handler._p5_verify = mock_verify

        with patch("app.services.workspace_service.workspace_path", return_value=ws):
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        assert result["validation_plan"]["can_be_completed"] is False
        assert result["status"] == "blocked"
        # 能力分区仍作为非门禁事实带出，但未翻转门禁
        assert "dimension_capabilities" in result


# ── (e) GAP-P5-3 .NET SDK-aware 命令补全 ────────────────────────────────────

class TestDotnetSdkAwareCommands:

    def _dotnet_workspace(self, tmp_path):
        ws = tmp_path / "projects" / "microoa"
        (ws / "src").mkdir(parents=True, exist_ok=True)
        (ws / "src" / "MicroOA.csproj").write_text("<Project></Project>", encoding="utf-8")
        return ws

    def test_dotnet_absent_stays_needs_user_input_not_fake_pass(self, tmp_path):
        from app.services.p5_command_service import P5CommandDetectionService
        ws = self._dotnet_workspace(tmp_path)
        with patch("app.services.p5_command_service.workspace_path", return_value=ws), \
             patch("app.services.p5_command_service.shutil.which", return_value=None):
            res = P5CommandDetectionService().detect_commands("microoa")
        assert res.project_type == "dotnet"
        assert res.dotnet_sdk_present is False
        # 无 SDK → 命令仍未补全（不 echo 假 pass），诚实 needs_user_input
        assert res.build_cmd is None
        assert any(n["slot"] == "build" for n in res.needs_user_input)
        # capability-first：记能力已接线待环境真验
        assert res.capability_notes
        assert res.capability_notes[0]["capability_ready"] is True

    def test_dotnet_present_fills_real_commands(self, tmp_path):
        from app.services.p5_command_service import P5CommandDetectionService
        ws = self._dotnet_workspace(tmp_path)
        with patch("app.services.p5_command_service.workspace_path", return_value=ws), \
             patch("app.services.p5_command_service.shutil.which", return_value="/usr/bin/dotnet"):
            res = P5CommandDetectionService().detect_commands("microoa")
        assert res.project_type == "dotnet"
        assert res.dotnet_sdk_present is True
        assert res.build_cmd == "dotnet build -warnaserror"
        assert res.test_cmd == "dotnet test --nologo"
        assert res.static_check_cmd == "dotnet format --verify-no-changes"
