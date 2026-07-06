"""R12-3-C5 P5 全量真实构建/运行/测试/静态检查命令服务测试。

覆盖：
  - P5CommandDetectionService：识别各种项目类型的命令
  - P5CommandExecutionService：经 ExecutionProvider 真实执行
  - 命令不可识别 → needs_user_input（诚实）
  - 命令执行失败 → validation_failed（≠ completed）
  - L4/L5 高风险 → Gate 拦截
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.p5_command_service import (
    P5CommandDetectionService, P5CommandExecutionService,
    P5DetectedCommands,
)


# ── 命令检测 ───────────────────────────────────────────────────────────────

class TestCommandDetection:

    def test_detect_python_project(self, tmp_path):
        ws = tmp_path / "projects" / "py_proj"
        ws.mkdir(parents=True)
        (ws / "pyproject.toml").write_text("[project]", encoding="utf-8")
        svc = P5CommandDetectionService()
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            result = svc.detect_commands("py_proj")
        assert result.project_type == "python"
        assert result.build_cmd is not None
        assert result.test_cmd is not None

    def test_detect_java_maven(self, tmp_path):
        ws = tmp_path / "projects" / "java_proj"
        ws.mkdir(parents=True)
        (ws / "pom.xml").write_text("<project>", encoding="utf-8")
        svc = P5CommandDetectionService()
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            result = svc.detect_commands("java_proj")
        assert result.project_type == "java/maven"
        assert result.build_cmd == "mvn compile -q"

    def test_detect_unknown_project_needs_user_input(self, tmp_path):
        """无法识别的项目类型 → needs_user_input（诚实，不伪造命令）。"""
        ws = tmp_path / "projects" / "unknown_proj"
        ws.mkdir(parents=True)
        (ws / "README.md").write_text("# unknown", encoding="utf-8")
        svc = P5CommandDetectionService()
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            result = svc.detect_commands("unknown_proj")
        assert result.project_type is None
        assert not result.all_identified
        assert len(result.needs_user_input) > 0


# ── 命令执行 ───────────────────────────────────────────────────────────────

class TestCommandExecution:

    def test_execute_successful_command(self, tmp_path):
        """成功命令 → passed=True + validated。"""
        ws = tmp_path / "projects" / "p"
        ws.mkdir(parents=True)
        mock_provider = MagicMock()
        async def _mock_execute(*a, **kw):
            return {"exit_code": 0, "stdout": "build ok", "stderr": "",
                    "blocked": False, "risk_level": "L1"}
        mock_provider.execute = _mock_execute
        svc = P5CommandExecutionService(tracer=MagicMock(), auditor=MagicMock())
        with patch("app.services.p5_command_service.get_execution_provider",
                   return_value=mock_provider), \
             patch("app.services.p5_command_service.workspace_path", return_value=ws), \
             patch("app.services.p5_command_service.P5CommandExecutionService._run_in_loop") as mock_run:
            mock_run.return_value = {"exit_code": 0, "stdout": "build ok",
                                     "stderr": "", "blocked": False, "risk_level": "L1"}
            result = svc.execute_slot_command("p", "build_verified", "echo ok")

        assert result.passed is True
        assert result.status == "validated"
        assert result.exit_code == 0

    def test_execute_failed_command(self, tmp_path):
        """失败命令 → passed=False + validation_failed（≠ completed）。"""
        ws = tmp_path / "projects" / "p"
        ws.mkdir(parents=True)
        svc = P5CommandExecutionService(tracer=MagicMock(), auditor=MagicMock())
        with patch("app.services.p5_command_service.get_execution_provider"), \
             patch("app.services.p5_command_service.workspace_path", return_value=ws), \
             patch("app.services.p5_command_service.P5CommandExecutionService._run_in_loop") as mock_run:
            mock_run.return_value = {"exit_code": 1, "stdout": "",
                                     "stderr": "compilation error",
                                     "blocked": False, "risk_level": "L2"}
            result = svc.execute_slot_command("p", "build_verified", "make")

        assert result.passed is False
        assert result.status == "validation_failed"
        assert result.exit_code == 1

    def test_execute_l4_command_gate_intercept(self, tmp_path):
        """L4/L5 高风险命令 → gate_required=True。"""
        ws = tmp_path / "projects" / "p"
        ws.mkdir(parents=True)
        svc = P5CommandExecutionService(tracer=MagicMock(), auditor=MagicMock())
        with patch("app.services.p5_command_service.get_execution_provider"), \
             patch("app.services.p5_command_service.workspace_path", return_value=ws), \
             patch("app.services.p5_command_service.P5CommandExecutionService._run_in_loop") as mock_run:
            mock_run.return_value = {"exit_code": 0, "stdout": "",
                                     "stderr": "", "blocked": False, "risk_level": "L4"}
            result = svc.execute_slot_command("p", "build_verified", "chmod 777 /")

        assert result.gate_required is True
        assert result.status == "needs_user_input"

    def test_no_command_does_not_fake_completed(self, tmp_path):
        """命令不可识别 → needs_user_input（不伪造 completed）。"""
        ws = tmp_path / "projects" / "p"
        ws.mkdir(parents=True)
        svc = P5CommandExecutionService(tracer=MagicMock(), auditor=MagicMock())
        cmds = P5DetectedCommands(project_id="p")  # 无命令
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            results = svc.execute_all_conditional("p", cmds)

        for r in results:
            if not r.command:  # 无命令的槽位
                assert r.passed is False
                # run_verified 无命令 → NOT_APPLICABLE（R12-14 库类工程）；其余 needs_user_input
                if r.slot_id == "run_verified":
                    assert r.status == "not_applicable"
                else:
                    assert r.status == "needs_user_input"
