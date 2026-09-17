"""R21 dsh 范式吸收「循环卫生」② —— 超时结果规范化（ExecutionProvider.execute()).

背景：dsh 对声明了超时的工具调用，把结果规范化为模型可读的清晰错误，而不是让调用方
看到一个笼统的异常。核实（读代码）rebuild 现状：`execution_provider.py` 里所有超时
分支此前只在 `stderr` 文本里写"Timeout after Ns"/"…timed out…"，**没有任何一处**
返回结构里带 `timed_out`（或等价）布尔字段，调用方只能靠字符串匹配 stderr 才能分辨
"超时"与"其它异常"——这正是"笼统异常"问题的真实表现，不是"字段互相嵌套依赖"的写法
（`exit_code` 本身在超时时一直都有填充），而是**关键事实字段完全缺失**。

本文件锁住修复后的契约：`timed_out` / `exit_code` / `signal` 三个事实各自独立、
真实地出现在每条 `execute()` 返回字典里（超时 / 成功 / 环境不可用 三种场景都覆盖），
不改变任何既有字段的名称或类型（`exit_code` 超时时仍是既有的 -1 语义，只是新增
`timed_out`/`signal` 两个字段，不做破坏性变更）。
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.services.execution_provider import (
    ContainerExecutionProvider,
    LocalSubprocessExecutionProvider,
    ToolchainContainerExecutionProvider,
    WorkspaceLocalExecutionProvider,
)


# ══════════════════════════════════════════════════════════════════
# LocalSubprocessExecutionProvider —— 真实子进程，不打桩（务实验证）
# ══════════════════════════════════════════════════════════════════

class TestLocalSubprocessTimeoutFields:

    def test_real_timeout_reports_independent_fields(self):
        """真实触发 asyncio.TimeoutError：sleep 2s，超时上限 0.3s。"""
        provider = LocalSubprocessExecutionProvider()
        result = asyncio.run(provider.execute(
            "import time; time.sleep(2)", language="python", timeout=0.3))

        assert result["timed_out"] is True
        # exit_code 保留既有 -1 语义（未变更类型/含义），且【始终存在】——不因超时分支
        # 被整体跳过。
        assert result["exit_code"] == -1
        assert "signal" in result
        # R21 实质缺陷修复后更新：超时分支现在会**真正终止子进程并等它停稳**（原实现
        # 只取消 communicate()，子进程继续跑成孤儿）。因此"未主动 kill ⇒ signal is None"
        # 这条断言锁定的是已经过时的行为；现在 signal 如实反映真实归因：sleep 中的 python
        # 被 SIGTERM 杀掉 ⇒ returncode -15 ⇒ 15。（SIGTERM 无效时升级 SIGKILL→9 的路径，
        # 见 tests/test_r21_subprocess_timeout_orphan_kill.py。）
        assert result["signal"] == 15
        assert result["blocked"] is False
        assert result["fallback"] is False

    def test_real_success_reports_timed_out_false_and_signal_none(self):
        provider = LocalSubprocessExecutionProvider()
        result = asyncio.run(provider.execute(
            "print('ok')", language="python", timeout=5))

        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        assert result["signal"] is None

    def test_security_denial_is_not_mislabelled_as_timeout(self):
        """L5 安检拒绝不是超时——timed_out 必须独立报 False，不能被安检分支带偏。"""
        provider = LocalSubprocessExecutionProvider()
        result = asyncio.run(provider.execute("sudo rm -rf /", language="bash", timeout=5))

        assert result["blocked"] is True
        assert result["timed_out"] is False
        assert result["signal"] is None


# ══════════════════════════════════════════════════════════════════
# WorkspaceLocalExecutionProvider —— 真实子进程（无白名单，P5 内部命令）
# ══════════════════════════════════════════════════════════════════

class TestWorkspaceLocalTimeoutFields:

    def test_real_timeout_reports_independent_fields(self, tmp_path):
        provider = WorkspaceLocalExecutionProvider()
        result = asyncio.run(provider.execute(
            "sleep 2", language="bash", timeout=0.3, cwd=str(tmp_path)))

        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        # R21 实质缺陷修复后更新（同上）：超时后子进程被真正终止 ⇒ `sleep` 被 SIGTERM
        # 杀掉，signal 如实报 15，而不再是"没杀 ⇒ None"。
        assert result["signal"] == 15

    def test_real_success_reports_timed_out_false_and_signal_none(self, tmp_path):
        provider = WorkspaceLocalExecutionProvider()
        result = asyncio.run(provider.execute(
            "echo hi", language="bash", timeout=5, cwd=str(tmp_path)))

        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        assert result["signal"] is None


# ══════════════════════════════════════════════════════════════════
# ContainerExecutionProvider —— docker 打桩（不依赖真实 docker 守护）
# ══════════════════════════════════════════════════════════════════

class TestContainerExecutionProviderTimeoutFields:

    def test_timeout_path_reports_independent_fields(self):
        import docker

        mock_container = MagicMock()
        mock_container.wait.side_effect = Exception("simulated docker wait timeout")
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        provider = ContainerExecutionProvider()
        with patch.object(docker, "from_env", return_value=mock_client):
            result = asyncio.run(provider.execute("print('x')", language="python", timeout=1))

        assert result["timed_out"] is True
        assert result["exit_code"] == -1  # 既有语义不变
        # kill() 是 MagicMock，调用不抛异常 → 真实发生了一次 SIGKILL
        assert result["signal"] == 9
        mock_container.kill.assert_called_once()

    def test_success_path_reports_timed_out_false_and_signal_none(self):
        import docker

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = (
            lambda stdout=False, stderr=False: b"ok\n" if stdout else b"")
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        provider = ContainerExecutionProvider()
        with patch.object(docker, "from_env", return_value=mock_client):
            result = asyncio.run(provider.execute("print('ok')", language="python", timeout=5))

        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        assert result["signal"] is None
        mock_container.kill.assert_not_called()

    def test_docker_unavailable_is_not_mislabelled_as_timeout(self):
        """Docker 守护不可用 ≠ 超时——两者是不同的失败面，不得混报。"""
        import docker
        from docker.errors import DockerException

        provider = ContainerExecutionProvider()
        with patch.object(docker, "from_env", side_effect=DockerException("no daemon")):
            result = asyncio.run(provider.execute("print('x')", language="python", timeout=1))

        assert result["timed_out"] is False
        assert result["signal"] is None
        assert result["exit_code"] == -1


# ══════════════════════════════════════════════════════════════════
# ToolchainContainerExecutionProvider —— docker 打桩
# ══════════════════════════════════════════════════════════════════

def _mk_toolchain_provider(tmp_path) -> ToolchainContainerExecutionProvider:
    return ToolchainContainerExecutionProvider(
        image_ref="fake/sdk:1",
        src_dir=str(tmp_path / "src"),
        build_dir=str(tmp_path / "build"),
        package_cache_dir=str(tmp_path / "cache"),
    )


class TestToolchainContainerTimeoutFields:

    def test_timeout_path_reports_independent_fields(self, tmp_path):
        import docker

        provider = _mk_toolchain_provider(tmp_path)
        mock_container = MagicMock()
        mock_container.wait.side_effect = Exception("simulated build timeout")
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        with patch.object(docker, "from_env", return_value=mock_client):
            result = asyncio.run(provider.execute("dotnet build", language="bash", timeout=1))

        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        assert result["signal"] == 9
        assert result["toolchain_unavailable"] is False  # 超时不是"环境不可用"

    def test_success_path_reports_timed_out_false_and_signal_none(self, tmp_path):
        import docker

        provider = _mk_toolchain_provider(tmp_path)
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b""
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        with patch.object(docker, "from_env", return_value=mock_client):
            result = asyncio.run(provider.execute("dotnet build", language="bash", timeout=5))

        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        assert result["signal"] is None

    def test_docker_unavailable_is_not_mislabelled_as_timeout(self, tmp_path):
        import docker
        from docker.errors import DockerException

        provider = _mk_toolchain_provider(tmp_path)
        with patch.object(docker, "from_env", side_effect=DockerException("no daemon")):
            result = asyncio.run(provider.execute("dotnet build", language="bash", timeout=1))

        assert result["timed_out"] is False
        assert result["signal"] is None
        assert result["toolchain_unavailable"] is True

    def test_kill_failure_still_reports_timed_out_true_with_unknown_signal(self, tmp_path):
        """kill() 本身失败时，timed_out 仍须真实报 True；但 signal 不可编造为 9
        （不知道进程到底有没有真的被信号终止），须诚实报 None。"""
        import docker

        provider = _mk_toolchain_provider(tmp_path)
        mock_container = MagicMock()
        mock_container.wait.side_effect = Exception("simulated build timeout")
        mock_container.kill.side_effect = Exception("container already exited")
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        with patch.object(docker, "from_env", return_value=mock_client):
            result = asyncio.run(provider.execute("dotnet build", language="bash", timeout=1))

        assert result["timed_out"] is True
        assert result["signal"] is None
