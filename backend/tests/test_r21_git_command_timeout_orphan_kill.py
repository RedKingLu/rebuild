"""R21 漏网点：`git_service.run_git_command` 超时后必须真正终止 git 进程（不留孤儿）。

背景（读代码核实的原状）：`run_git_command` 与刚修完的 `execution_provider._run_subprocess`
有**一模一样**的缺陷 —— 只做 `asyncio.wait_for(proc.communicate(), timeout=…)`，`wait_for`
取消的只是协程，**git 进程本身完全没被终止**。真实影响：`git clone` 大仓库超时后会留下一个
持续下载的孤儿进程，占网络和磁盘；平台的项目接入流程（D-058 Git 接入，
`app/api/routes_integrations.py` 的 clone/pull/push 端点）会真实走到这条路径。

修法**刻意不在 git_service 里复制一份 SIGTERM/SIGKILL 逻辑** —— 那正是本仓刚因"脱敏模式表
有两份副本悄悄漂移"（`B-R20-REDACT-THREE-IMPLS`）踩过的坑。清理原语抽到
`app/services/subprocess_runner.py`，`ExecutionProvider` 与 git 两个调用方共用同一份。

本文件锁住的契约：
  ① 超时 → `timed_out=True`，且 **git 进程与它拉起的 helper 孙进程实测都已不存在**
     （不只断言返回字段）；
  ② 既有四个返回键（`exit_code`/`stdout`/`stderr`/`success`）名称与语义一字不变，
     新增的 `timed_out`/`signal` 是**纯新增可选键**；
  ③ 上层取消同样不留孤儿，且取消语义照常向上传播；
  ④ 非超时失败面（git 不存在 / git 返回非 0）不得被误报成超时；
  ⑤ **单一实现守卫**：终止逻辑只允许存在于 `subprocess_runner.py` 一处。

真实 git 构造说明（为什么等价、为什么不用真网络）：用
`git -c protocol.ext.allow=always clone "ext::sleep <N>" <dst>` —— 这是**真实的
`git clone`**，走 git 自己的 transport 层，只是把远端 helper 换成一个必然超时的命令。
实测进程树与真实 clone 同形（`git clone` → `git remote-ext` → helper 命令，同一进程组），
因此它同时验证了"直接子进程被终止"和"helper 孙进程不被 reparent 成孤儿"。
选它而不用真仓库 clone：真网络 clone 依赖外网与仓库大小、耗时不可控、CI 不可复现；
选它而不用 `sleep` 之类等价慢命令：本文件要验的是 **`run_git_command` 这条路径**，
被执行的必须是真的 git 二进制（含它自己的信号处理与子进程拉起行为）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from app.services.git_service import run_git_command

# 复用兄弟文件里已验证过的活性探测（`/proc/<pid>/stat` 判活、僵尸视为已死）与 spawn 探针，
# 不再抄第三份 —— 与本次修复同一条纪律：同一机制只留一处实现。
from tests.test_r21_subprocess_timeout_orphan_kill import (  # noqa: E402
    _SpawnSpy,
    _wait_until_dead,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None,
                                reason="本文件要跑真实 git 二进制，环境里没有 git")

_HAS_PROC = Path("/proc/self/cmdline").exists()

# 后代进程标记：用一个本进程唯一、不会与系统上其它进程撞车的 sleep 秒数，事后靠扫
# /proc/*/cmdline 判断"git 拉起的 helper 是否还在"（无需解析 ps 输出）。
_DESCENDANT_MARKER = f"864{os.getpid()}"
_HANGING_REMOTE = f"ext::sleep {_DESCENDANT_MARKER}"

_POLL_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05

# 既有返回契约：这四个键的名称与语义**一字不变**（调用方众多，见 routes_integrations.py）。
_LEGACY_KEYS = {"exit_code", "stdout", "stderr", "success"}
# R21 新增的可选键（纯新增，不动上面四个）。
_ADDED_KEYS = {"timed_out", "signal"}

# 编码规范（.claude/rules/common/coding-style.md）：200-400 行典型、800 行上限。
_MAX_FILE_LINES = 800


def _pids_matching(marker: str) -> list[int]:
    """扫 /proc 找出 cmdline 里含 marker 的进程（git 的 helper 孙进程）。"""
    found: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                raw = fh.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
        if marker.encode() in raw:
            found.append(int(entry))
    return found


def _wait_until_no_descendant(marker: str, timeout: float = _POLL_TIMEOUT_S) -> list[int]:
    """等到不再有含 marker 的进程；返回超时时仍存活的 pid 列表（空 = 清理干净）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = _pids_matching(marker)
        if not alive:
            return []
        time.sleep(_POLL_INTERVAL_S)
    return _pids_matching(marker)


def _hanging_clone(dst: Path, timeout: float) -> dict:
    """一次必然超时的**真实 `git clone`**（远端 helper 换成必然挂住的命令）。"""
    return asyncio.run(run_git_command(
        "-c", "protocol.ext.allow=always",
        "clone", _HANGING_REMOTE, str(dst),
        timeout=timeout,
    ))


# ══════════════════════════════════════════════════════════════════
# ① 超时 → git 进程与它的 helper 孙进程真的都没了（核心回归：孤儿进程）
# ══════════════════════════════════════════════════════════════════

class TestGitTimeoutActuallyKillsProcess:

    def test_timeout_git_process_is_really_gone(self, monkeypatch, tmp_path):
        """真实 `git clone` 超时：返回 timed_out=True，且 git 进程**实测已不存在**。"""
        spy = _SpawnSpy(monkeypatch)

        result = _hanging_clone(tmp_path / "clone-a", timeout=1)

        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        assert result["success"] is False
        assert "Timeout after" in result["stderr"]

        proc = spy.proc
        # 关键断言：不是"发过信号"，而是进程**已经收尾**（returncode 已确定）。
        assert proc.returncode is not None, "超时后 git 进程仍未停止 → 孤儿进程"
        assert _wait_until_dead(proc.pid), f"git pid {proc.pid} 在超时清理后仍存活"
        # 进程被确认终止 ⇒ signal 必须是真实归因（SIGTERM 生效→15 / 升级→9），不得为 None。
        # 本机实测（git 2.43.0）为 15：`git clone` 用 SIGTERM 默认处置即可停下。这里
        # 刻意不写死 15 —— 不同 git 版本是否安装 TERM 处理器不受本平台控制，两级升级
        # （TERM 无效 → KILL）的精确归因由 test_r21_subprocess_timeout_orphan_kill.py 锁住。
        assert result["signal"] in (15, 9), (
            f"进程已确认终止，signal 必须如实归因，实际 {result['signal']!r}")

    @pytest.mark.skipif(not _HAS_PROC, reason="后代进程扫描依赖 /proc")
    def test_timeout_kills_git_remote_helper_grandchildren(self, tmp_path):
        """`git clone` 拉起的 `git remote-ext` + helper 命令（孙/曾孙进程）也必须被清掉。

        只 kill 直接子进程（`git clone`）时，这些后代会被 reparent 给 init 继续跑 ——
        这正是"超时的 clone 还在后台持续下载"的真实形态。
        """
        result = _hanging_clone(tmp_path / "clone-b", timeout=1)

        assert result["timed_out"] is True
        leaked = _wait_until_no_descendant(_DESCENDANT_MARKER)
        assert leaked == [], (
            f"git 的 helper 后代进程 {leaked} 在超时清理后仍存活 → 进程组未被清理")

    def test_cancellation_also_kills_git_and_propagates(self, monkeypatch, tmp_path):
        """上层取消（放弃等待）同样不能留孤儿：进程被 SIGKILL，取消语义照常向上传播。"""
        spy = _SpawnSpy(monkeypatch)

        async def scenario():
            task = asyncio.create_task(run_git_command(
                "-c", "protocol.ext.allow=always",
                "clone", _HANGING_REMOTE, str(tmp_path / "clone-c"),
                timeout=60,
            ))
            await asyncio.sleep(0.5)      # 等 git 真的起来
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        assert _wait_until_dead(spy.proc.pid), "取消后 git 进程仍存活 → 孤儿进程"
        if _HAS_PROC:
            assert _wait_until_no_descendant(_DESCENDANT_MARKER) == []


# ══════════════════════════════════════════════════════════════════
# ② 返回契约：四个既有键一字不变，timed_out/signal 是纯新增
# ══════════════════════════════════════════════════════════════════

class TestReturnContractIsAdditiveOnly:

    def test_timeout_keeps_legacy_keys_and_only_adds_two(self, tmp_path):
        result = _hanging_clone(tmp_path / "clone-d", timeout=1)

        assert _LEGACY_KEYS <= set(result), "既有键不得缺失"
        assert set(result) == _LEGACY_KEYS | _ADDED_KEYS, (
            f"只允许新增 {sorted(_ADDED_KEYS)} 两个键，实际 {sorted(set(result) - _LEGACY_KEYS)}")
        # 四个既有键的语义与原实现完全一致（超时：-1 / "" / "Timeout after Ns" / False）
        assert result["exit_code"] == -1
        assert result["stdout"] == ""
        assert result["stderr"].startswith("Timeout after 1s")
        assert result["success"] is False

    def test_success_path_contract_unchanged(self):
        """只读的 `git --version`：既有四键语义不变，新增键如实报"没超时/无信号"。"""
        result = asyncio.run(run_git_command("--version", timeout=10))

        assert result["exit_code"] == 0
        assert result["success"] is True
        assert "git version" in result["stdout"]
        assert result["stderr"] == ""
        assert result["timed_out"] is False
        assert result["signal"] is None
        assert set(result) == _LEGACY_KEYS | _ADDED_KEYS


# ══════════════════════════════════════════════════════════════════
# ④ 非超时失败面不得被误报成超时
# ══════════════════════════════════════════════════════════════════

class TestOtherFailuresAreNotMislabelledAsTimeout:

    def test_nonzero_exit_is_not_reported_as_timeout(self, tmp_path):
        """在非仓库目录跑只读的 `git rev-parse HEAD`：真实非 0 退出，不是超时。"""
        result = asyncio.run(run_git_command(
            "rev-parse", "--verify", "HEAD", cwd=str(tmp_path), timeout=10))

        assert result["exit_code"] != 0
        assert result["success"] is False
        assert result["timed_out"] is False
        assert result["signal"] is None
        assert result["stderr"], "git 的真实报错必须原样带回"

    def test_missing_git_binary_is_not_reported_as_timeout(self, monkeypatch, tmp_path):
        """git 不在 PATH（起不来）：保留既有 "git command not found" 措辞，不是超时。

        进程根本没起来 ⇒ 没有进程需要清理，`signal` 诚实报 None。
        """
        async def _raise_not_found(*_a, **_kw):
            raise FileNotFoundError("simulated: no such file or directory: 'git'")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_not_found)

        result = asyncio.run(run_git_command("status", cwd=str(tmp_path), timeout=10))

        assert result["exit_code"] == -1
        assert result["stderr"] == "git command not found"
        assert result["success"] is False
        assert result["timed_out"] is False
        assert result["signal"] is None

    def test_spawn_failure_other_than_missing_git_is_reported_verbatim(
            self, monkeypatch, tmp_path):
        """其它起不来的原因（如 cwd 不存在）如实带回原始错误文本，也不是超时。"""
        result = asyncio.run(run_git_command(
            "status", cwd=str(tmp_path / "missing-dir"), timeout=10))

        assert result["exit_code"] == -1
        assert result["success"] is False
        assert result["timed_out"] is False
        assert result["signal"] is None
        assert result["stderr"]


# ══════════════════════════════════════════════════════════════════
# ⑤ 单一实现守卫：终止逻辑只允许存在于 subprocess_runner.py
# ══════════════════════════════════════════════════════════════════

_SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
_RUNNER = _SERVICES / "subprocess_runner.py"
_CALLERS = (_SERVICES / "git_service.py", _SERVICES / "execution_provider.py")


class TestSingleTerminationImplementation:
    """B-R20-REDACT-THREE-IMPLS 教训：同一机制不得存在第二份可漂移的副本。"""

    @staticmethod
    def _code_names(path: Path) -> set[str]:
        """只取【代码取值】里的标识符：注释与字符串/文档串里提到 SIGTERM 属说明性文字，
        不是第二份实现（手法同 `test_r19_1_toolchain_container` 的 tokenize 检查）。"""
        import io
        import tokenize
        text = path.read_text(encoding="utf-8")
        names: set[str] = set()
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME:
                names.add(tok.string)
        return names

    def test_runner_owns_the_termination_primitives(self):
        names = self._code_names(_RUNNER)
        for token in ("SIGTERM", "SIGKILL", "killpg",
                      "SUBPROCESS_TERM_GRACE_S", "SUBPROCESS_KILL_REAP_TIMEOUT_S"):
            assert token in names, f"清理原语应归属 subprocess_runner.py，缺 {token}"

    @pytest.mark.parametrize("path", _CALLERS, ids=lambda p: p.name)
    def test_callers_delegate_and_hold_no_second_copy(self, path):
        names = self._code_names(path)
        assert "run_subprocess_command" in names, f"{path.name} 必须复用统一的执行入口"
        for token in ("SIGTERM", "SIGKILL", "killpg", "send_signal",
                      "SUBPROCESS_TERM_GRACE_S", "SUBPROCESS_KILL_REAP_TIMEOUT_S"):
            assert token not in names, (
                f"{path.name} 的代码里出现了第二份终止逻辑（{token}）—— 必须只留 "
                f"subprocess_runner.py 一处")

    @pytest.mark.parametrize("path", (_RUNNER, *_CALLERS), ids=lambda p: p.name)
    def test_files_stay_within_the_line_limit(self, path):
        n = len(path.read_text(encoding="utf-8").splitlines())
        assert n <= _MAX_FILE_LINES, (
            f"{path.name} 有 {n} 行，超过 {_MAX_FILE_LINES} 行上限（编码规范）")
