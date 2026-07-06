"""R12-8 回归修复测试：WorkspaceExecutionProvider + P6 路由 NameError。

不打桩 _run_in_loop（真实沙箱执行），新增 /p6/package 路由单测。
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.execution_provider import (
    get_execution_provider, WorkspaceLocalExecutionProvider,
)
from app.services.p5_command_service import (
    P5CommandDetectionService, P5CommandExecutionService,
)


# ── WorkspaceLocalExecutionProvider 真实执行 ──────────────────────────────

class TestWorkspaceProvider:

    def test_python_pass(self, tmp_path):
        """python3 成功命令 → exit 0。"""
        ws = tmp_path / "test_ws"
        ws.mkdir()
        (ws / "hello.py").write_text("print('hello')", encoding="utf-8")
        provider = WorkspaceLocalExecutionProvider()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                provider.execute("python3 hello.py", language="bash",
                                 timeout=10, cwd=str(ws)))
        finally:
            loop.close()
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_python_fail(self, tmp_path):
        """失败命令 → 非零 exit（不掩盖）。"""
        ws = tmp_path / "test_ws"
        ws.mkdir()
        provider = WorkspaceLocalExecutionProvider()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                provider.execute("python3 -c \"import sys; sys.exit(1)\"",
                                 language="bash", timeout=10, cwd=str(ws)))
        finally:
            loop.close()
        assert result["exit_code"] == 1

    def test_dangerous_blocked(self):
        """sudo/rm -rf 等 DENY 子串 → blocked。"""
        provider = WorkspaceLocalExecutionProvider()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                provider.execute("rm -rf /", language="bash", timeout=10))
        finally:
            loop.close()
        assert result["blocked"] is True
        assert result["risk_level"] == "L5"

    def test_dotnet_marker_match(self, tmp_path):
        """R12-12：.sln/.csproj/.vbproj 扩展名能正确识别为 dotnet 类型。"""
        ws = tmp_path / "test_ws"
        ws.mkdir()
        (ws / "MyApp.sln").write_text("Microsoft Studio Solution", encoding="utf-8")
        (ws / "MyApp.csproj").write_text("<Project/>", encoding="utf-8")
        svc = P5CommandDetectionService()
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            result = svc.detect_commands("test_proj")
        assert result.project_type is not None
        assert "dotnet" in result.project_type

    def test_python_static_uses_compileall(self, tmp_path):
        """R12-14：python static_check 用 compileall（可以处理目录），非 py_compile。"""
        ws = tmp_path / "test_ws"
        ws.mkdir()
        (ws / "ok.py").write_text("x = 1\n", encoding="utf-8")
        (ws / "pyproject.toml").write_text("[project]", encoding="utf-8")
        svc = P5CommandDetectionService()
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            result = svc.detect_commands("py_proj")
        assert result.static_check_cmd is not None
        assert "compileall" in result.static_check_cmd
        assert "py_compile" not in result.static_check_cmd
        # 真实执行验证（patch workspace_path 使 cwd 指向临时目录）
        exec_svc = P5CommandExecutionService(tracer=MagicMock(), auditor=MagicMock(),
                                              mode="workspace_local")
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            loop = asyncio.new_event_loop()
            try:
                vr = exec_svc.execute_slot_command("py_proj", "static_check",
                                                     result.static_check_cmd, timeout=30)
            finally:
                loop.close()
        assert vr.exit_code == 0
        assert vr.passed is True

    def test_run_verified_not_applicable_for_library(self, tmp_path):
        """R12-14：命令-less 工程的 run_verified 置 NOT_APPLICABLE（非永久 needs_user_input）。"""
        ws = tmp_path / "test_ws"
        ws.mkdir()
        (ws / "ok.py").write_text("def hello(): pass\n", encoding="utf-8")
        (ws / "pyproject.toml").write_text("[project]", encoding="utf-8")
        detect_svc = P5CommandDetectionService()
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            cmds = detect_svc.detect_commands("lib_proj")
        # python 规则无 run_cmd → None
        assert cmds.run_cmd is None
        # 执行后 run_verified 应为 NOT_APPLICABLE
        exec_svc = P5CommandExecutionService(tracer=MagicMock(), auditor=MagicMock(),
                                              mode="workspace_local")
        loop = asyncio.new_event_loop()
        try:
            results = exec_svc.execute_all_conditional("lib_proj", cmds)
        finally:
            loop.close()
        run_result = next(r for r in results if r.slot_id == "run_verified")
        assert run_result.status == "not_applicable"

    def test_dotnet_legacy_do_not_fakepass(self, tmp_path):
        """R12-12：dotnet/legacy 不通过 echo 伪造 validated。build/test 均需用户输入。"""
        ws = tmp_path / "test_ws"
        ws.mkdir()
        (ws / "web.config").write_text("<configuration/>", encoding="utf-8")
        (ws / "Global.asax").write_text("<%@ Application %>", encoding="utf-8")
        svc = P5CommandDetectionService()
        with patch("app.services.p5_command_service.workspace_path", return_value=ws):
            result = svc.detect_commands("test_legacy")
        assert result.project_type == "dotnet/legacy"
        # 不得有 echo 假命令给用户自动执行
        assert result.build_cmd is None
        # build 槽位需要用户输入（经 Gate 补录）
        assert any(g.get("slot") == "build" for g in result.needs_user_input)

    def test_non_python_command_allowed(self, tmp_path):
        """R12-10：非 python 命令（如 make/go 路径的 echo 模拟）不在白名单但仍可执行。"""
        ws = tmp_path / "test_ws"
        ws.mkdir()
        provider = WorkspaceLocalExecutionProvider()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                provider.execute("echo build_ok && echo test_ok", language="bash",
                                 timeout=10, cwd=str(ws)))
        finally:
            loop.close()
        assert result["exit_code"] == 0
        assert "build_ok" in result["stdout"]

    def test_get_provider_factory(self):
        """workspace_local 模式工厂。"""
        p = get_execution_provider(mode="workspace_local")
        assert isinstance(p, WorkspaceLocalExecutionProvider)


# ── P6 package 路由回归 ────────────────────────────────────────────────────

class TestP6Route:

    def test_route_imports_no_nameerror(self, client):
        """R12-8 修复：routes_p6_delivery 引用 RealP6Handler 正常 import。"""
        # 简单调用 /p6/package（无 P5 report → 422，但非 500 NameError）
        r = client.get('/api/projects/unknown/runs/unknown/p6/package')
        assert r.status_code == 422

    def test_download_source_blocked(self, client):
        """source/ 下载 → 403。"""
        r = client.get('/api/projects/unknown/runs/unknown/p6/download?path=source/x.py')
        assert r.status_code == 403
