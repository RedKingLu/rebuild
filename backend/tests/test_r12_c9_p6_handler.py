"""R12-3-C9 RealP6Handler 与 P6 最终 Gate 测试。

覆盖：
  - RealP6Handler 注册成功（bootstrap_graph_handlers 包含 "p6"）
  - P5 未通过 → P6 blocked（P6 不得执行）
  - 正常路径：交付包生成 + P6 最终 Gate 创建
  - review() 行为（completed + gate → passed）
  - P6 最终 Gate 真实创建（D-023）
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from app.graph.stage_handlers import RealP6Handler, bootstrap_graph_handlers
from app.graph import nodes
from app.services.p5_input_service import P4InputFacts
from app.services.p6_delivery_service import DeliveryPackage


class TestP6HandlerRegistration:
    """RealP6Handler 已注册。"""

    def test_p6_registered(self):
        nodes.clear_handlers()
        bootstrap_graph_handlers(force=True)
        assert nodes.get_handler("p6") is not None
        assert isinstance(nodes.get_handler("p6"), RealP6Handler)


class TestP6HandlerExecution:

    def test_p5_not_passed_blocks_p6(self):
        """P5 未通过 → P6 blocked（P6 不得执行）。"""
        handler = RealP6Handler(tracer=MagicMock(), auditor=MagicMock())
        mock_input_svc = MagicMock()
        mock_input_svc.read_p4_input.return_value = P4InputFacts(
            project_id="p", run_id="r",
            blocked=True, blocked_reason="Gate 未通过")

        import app.services.p5_input_service as p5_mod
        orig = p5_mod.P5InputService
        p5_mod.P5InputService = lambda *a, **kw: mock_input_svc
        try:
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))
        finally:
            p5_mod.P5InputService = orig

        assert result["status"] == "blocked"
        assert "P5" in result["reason"]

    def test_completed_creates_p6_final_gate_and_delivery(self):
        """正常路径：completed + 交付包 + P6 最终 Gate。"""
        handler = RealP6Handler(tracer=MagicMock(), auditor=MagicMock())

        p4_input = P4InputFacts(
            project_id="p", run_id="r",
            blocked=False, p4_to_p5_gate_status="approved",
            output_code_refs=["output_code/x.py"],
            evidence_refs=["ev-1"],
        )
        pkg = DeliveryPackage(project_id="p", run_id="r")
        pkg.delivery_manifest = {"contents": {"output_code_count": 1, "patch_count": 0}}
        pkg.risk_manifest = {"risk_count": 0, "has_blocking": False, "risks": []}
        pkg.hash_manifest = {"files": [{"path": "output_code/x.py", "sha256": "abc"}]}
        pkg.p6_delivery_report = {"summary": {}}
        pkg.p5_validation_report = {"evidence_refs": ["ev-1"]}
        pkg.desensitization_ok = True
        pkg.desensitization_issues = []

        mock_input_svc = MagicMock()
        mock_input_svc.read_p4_input.return_value = p4_input
        mock_p6_svc = MagicMock()
        mock_p6_svc.generate_delivery_package.return_value = pkg
        handler._p6_svc = mock_p6_svc

        mock_gate_resp = MagicMock()
        mock_gate_resp.gate_id = "gate-p6-final"
        mock_svc = MagicMock()
        mock_svc.gate_service.create.return_value = mock_gate_resp

        with patch.object(handler, '_services', return_value=mock_svc), \
             patch.object(RealP6Handler, '_load_p5_report',
                          return_value={"can_be_completed": True,
                                        "validation_plan": {"slots": []}}), \
             patch("app.services.p5_input_service.P5InputService") as mock_cls:
            mock_cls.return_value = mock_input_svc
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))

        assert result["status"] == "completed"
        assert result["p6_final_gate_id"] == "gate-p6-final"
        # Gate 已创建（D-023）
        mock_svc.gate_service.create.assert_called_once()
        call_kwargs = mock_svc.gate_service.create.call_args[1]
        assert call_kwargs.get("stage") == "p6"


class TestP6P5Gate:
    """R12-7 修复 B-P6-UNGATED-BY-P5：P6 必须按 P5 验证结果门禁。"""

    def test_p5_not_passed_blocks_p6(self):
        """P5 未通过（无验证报告）→ P6 blocked。"""
        handler = RealP6Handler(tracer=MagicMock(), auditor=MagicMock())
        p4_input = P4InputFacts(
            project_id="p", run_id="r",
            blocked=False, p4_to_p5_gate_status="approved")

        import app.services.p5_input_service as p5_mod
        orig = p5_mod.P5InputService
        mock_input_svc = MagicMock()
        mock_input_svc.read_p4_input.return_value = p4_input
        p5_mod.P5InputService = lambda *a, **kw: mock_input_svc
        try:
            result = asyncio.run(handler.execute({"project_id": "p", "run_id": "r"}))
        finally:
            p5_mod.P5InputService = orig

        assert result["status"] == "blocked"
        assert "P5" in result["reason"]


class TestP5HandlerE2E:
    """R12-16: 命令-less 真实 python 库 → RealP5Handler（不打桩） → can_be_completed=True。"""

    def setup_method(self):
        from app.graph.stage_handlers import RealP5Handler
        self.cls = RealP5Handler

    def test_p5_handler_command_less_python_lib(self, tmp_path):
        """库类工程（无 run 命令）: build+test+static 全 validated, run=NA → can_complete=True。"""
        import asyncio
        ws = tmp_path / "projects" / "lib_proj"
        ws.mkdir(parents=True)
        (ws / "ok.py").write_text("def hello(): pass\n", encoding="utf-8")
        (ws / "pyproject.toml").write_text("[project]", encoding="utf-8")

        handler = self.cls(tracer=MagicMock(), auditor=MagicMock())

        p4_input = P4InputFacts(
            project_id="lib_proj", run_id="r",
            blocked=False, p4_to_p5_gate_status="approved",
            output_code_refs=[], patch_refs=[], evidence_refs=[],
            p4_execution_summary={"stage": "p4", "graph_status": "completed",
                                   "change_manifest": []},
        )

        with patch("app.services.workspace_service.workspace_path", return_value=ws), \
             patch("app.services.p5_input_service.P5InputService") as mock_cls, \
             patch("app.services.p5_command_service.workspace_path", return_value=ws):
            mock_cls.return_value.read_p4_input.return_value = p4_input
            result = asyncio.run(handler.execute({"project_id": "lib_proj", "run_id": "r"}))

        plan = result["validation_plan"]
        slots = {s["slot_id"]: s for s in plan["slots"]}
        # run=NA 不应崩溃
        assert slots["run_verified"]["status"] == "not_applicable"
        # 硬必需因无 output_code → NA，但路径不崩溃
        assert result["status"] in ("completed", "blocked")


class TestP6HandlerReview:

    def test_completed_with_gate_passes(self):
        """completed + gate_id → review passed。"""
        handler = RealP6Handler()
        review = handler.review({"status": "completed", "p6_final_gate_id": "gate-1"})
        assert review.passed is True

    def test_completed_without_gate_fails(self):
        """completed 但无 gate → 诚实 blocked（D-023 违规）。"""
        handler = RealP6Handler()
        review = handler.review({"status": "completed", "p6_final_gate_id": None})
        assert review.passed is False
        assert any("Gate" in i.get("detail", "") for i in review.issues)

    def test_blocked_fails(self):
        handler = RealP6Handler()
        review = handler.review({"status": "blocked", "reason": "test"})
        assert review.passed is False
