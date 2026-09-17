"""R21 实质缺陷：`_run_subprocess` 超时后必须真正终止子进程（不留孤儿）。

背景（读代码核实的原状）：`execution_provider._run_subprocess` 的超时分支只做了
`asyncio.wait_for(proc.communicate(), timeout=...)` —— `wait_for` 取消的只是
`communicate()` 这个协程，**子进程本身完全没被终止**（原注释里甚至明写"此处进程未被
主动终止（未 kill）"）。P5 验证阶段跑真实构建/测试命令，超时上限可达 900s，一次超时的
`dotnet build` / `mvn` / 测试套件就会变成永久运行的孤儿进程，持续占 CPU/内存/文件锁，
平台长期运行不断累积——典型的"清理流程只请求停止、不等待真正停稳"。

本文件锁住修复后的契约：
  ① 超时 → `timed_out=True` 且**子进程确实已经不存在**（不只看返回字段，实测进程状态）；
  ② SIGTERM 杀不掉 → 升级 SIGKILL，且 `signal` 如实反映最终是 15 还是 9（不编造）；
  ③ `bash -c` 拉起的孙进程（后台任务）也被按进程组清掉，不留孤儿；
  ④ 竞态/清理失败（进程正好自己退出、killpg 抛 ProcessLookupError）不得让整个调用抛异常；
  ⑤ 正常（不超时）路径行为不变。

R21 拆分后（`execution_provider.py` 越过 800 行上限）：子进程生命周期原语已搬到
`app/services/subprocess_runner.py`（同一份实现现在也被 `git_service.run_git_command`
复用，见 `test_r21_git_command_timeout_orphan_kill.py`）。本文件**只改了被搬移符号的导入
路径与 monkeypatch 目标模块，断言一字未改** —— `_run_subprocess` 本体（语言→argv 映射、
ALLOWED_COMMANDS 白名单、_clean_env）仍在 `execution_provider`。
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.services.execution_provider import (
    WorkspaceLocalExecutionProvider,
    _run_subprocess,
)
from app.services.subprocess_runner import (
    SUBPROCESS_TERM_GRACE_S,
    _signal_from_returncode,
    _terminate_subprocess,
)

# 轮询进程消失的上限（秒）——SIGKILL 后内核拆除进程需要一点时间，但不该是"秒级以上"。
_LIVENESS_POLL_TIMEOUT_S = 3.0
_LIVENESS_POLL_INTERVAL_S = 0.05


def _is_alive(pid: int) -> bool:
    """进程是否**真的还活着**。

    僵尸（Linux /proc state `Z`）不算活着：它已经死了，只是尚未被父进程回收——
    而 `os.kill(pid, 0)` 对僵尸是成功的，只用 kill(0) 会把"已清理"误判成"还在跑"。
    """
    stat_path = f"/proc/{pid}/stat"
    if os.path.exists("/proc/self/stat"):
        try:
            with open(stat_path, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except (FileNotFoundError, ProcessLookupError):
            return False
        # 格式：pid (comm) state ...；comm 可含空格/括号 → 从最后一个 ')' 之后切。
        tail = raw.rsplit(")", 1)[-1].split()
        return bool(tail) and tail[0] != "Z"
    try:  # 非 Linux 兜底
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_dead(pid: int, timeout: float = _LIVENESS_POLL_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(_LIVENESS_POLL_INTERVAL_S)
    return not _is_alive(pid)


class _SpawnSpy:
    """记录 `_run_subprocess` 真实创建的子进程对象，用于事后核对它是否停稳。"""

    def __init__(self, monkeypatch):
        self.procs: list = []
        real = asyncio.create_subprocess_exec

        async def spy(*args, **kwargs):
            proc = await real(*args, **kwargs)
            self.procs.append(proc)
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    @property
    def proc(self):
        assert len(self.procs) == 1, f"期望恰好创建 1 个子进程，实际 {len(self.procs)}"
        return self.procs[0]


# ══════════════════════════════════════════════════════════════════
# ① 超时 → 子进程真的没了（核心回归：孤儿进程）
# ══════════════════════════════════════════════════════════════════

class TestTimeoutActuallyKillsChild:

    def test_timeout_child_process_is_really_gone(self, monkeypatch, tmp_path):
        """`sleep 30` + 1s 超时：返回 timed_out=True，且子进程**实测已不存在**。"""
        spy = _SpawnSpy(monkeypatch)

        result = asyncio.run(_run_subprocess(
            "sleep 30", "bash", 1, cwd=str(tmp_path), enforce_whitelist=False))

        assert result["timed_out"] is True
        assert result["exit_code"] == -1

        proc = spy.proc
        # 关键断言：不是"发过信号"，而是进程**已经收尾**（returncode 已确定）。
        assert proc.returncode is not None, "超时后子进程仍未停止 → 孤儿进程"
        assert _wait_until_dead(proc.pid), f"pid {proc.pid} 在超时清理后仍存活"
        # sleep 默认处理 SIGTERM → 被信号 15 终止，signal 如实反映
        assert result["signal"] == 15
        assert _signal_from_returncode(proc.returncode) == result["signal"]

    def test_timeout_escalates_to_sigkill_when_sigterm_ignored(self, monkeypatch, tmp_path):
        """忽略 SIGTERM 的子进程：宽限期后必须升级 SIGKILL，signal 如实报 9。

        `trap '' TERM` 设的 SIG_IGN 会被 exec 继承 ⇒ 整个进程组都无视 SIGTERM，
        只有 SIGKILL 能停下它——正是"只发个温和信号就返回"会漏掉的那类进程。
        """
        spy = _SpawnSpy(monkeypatch)

        started = time.monotonic()
        result = asyncio.run(_run_subprocess(
            "trap '' TERM; sleep 30", "bash", 1, cwd=str(tmp_path), enforce_whitelist=False))
        elapsed = time.monotonic() - started

        assert result["timed_out"] is True
        assert result["signal"] == 9, "SIGTERM 无效时必须升级到 SIGKILL 并如实上报"
        proc = spy.proc
        assert proc.returncode == -9
        assert _wait_until_dead(proc.pid)
        # 宽限期真的被等过（不是发完 TERM 立刻 KILL），但也没无界等待
        assert elapsed >= SUBPROCESS_TERM_GRACE_S
        assert elapsed < 1 + SUBPROCESS_TERM_GRACE_S + 10

    def test_timeout_kills_nested_grandchild_process_group(self, monkeypatch, tmp_path):
        """`bash -c` 里 `&` 起的孙进程也必须被清掉（按进程组终止）。

        只 kill 直接子进程（bash）时，孙进程会被 reparent 给 init 继续跑——这正是
        `mvn`/`dotnet build` 超时后留下一堆残留进程的形态。
        """
        pidfile = tmp_path / "grandchild.pid"
        code = f"sleep 30 & echo $! > {pidfile}; wait"

        result = asyncio.run(_run_subprocess(
            code, "bash", 1, cwd=str(tmp_path), enforce_whitelist=False))

        assert result["timed_out"] is True
        assert pidfile.exists(), "测试自身前提不成立：孙进程 pid 未落盘"
        grandchild_pid = int(pidfile.read_text().strip())
        assert _wait_until_dead(grandchild_pid), (
            f"孙进程 pid {grandchild_pid} 在超时清理后仍存活 → 进程组未被清理")

    def test_cancellation_also_kills_child_and_propagates(self, monkeypatch, tmp_path):
        """上层取消（放弃等待）同样不能留孤儿：进程被 SIGKILL，取消语义照常向上传播。"""
        spy = _SpawnSpy(monkeypatch)

        async def scenario():
            task = asyncio.create_task(_run_subprocess(
                "sleep 30", "bash", 60, cwd=str(tmp_path), enforce_whitelist=False))
            await asyncio.sleep(0.5)      # 等子进程真的起来
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        assert _wait_until_dead(spy.proc.pid), "取消后子进程仍存活 → 孤儿进程"


# ══════════════════════════════════════════════════════════════════
# ④ 竞态 / 清理失败：不得让整个调用抛异常
# ══════════════════════════════════════════════════════════════════

class TestCleanupFailuresDoNotBreakTheCall:

    def test_killpg_process_lookup_error_still_returns_honest_timeout(
            self, monkeypatch, tmp_path):
        """信号发不出去（进程正好自己退出，killpg 抛 ProcessLookupError）→ 不抛异常。

        子进程在宽限期内自行正常退出（sleep 0.5，超时 0.1）⇒ 无信号可归因，
        `signal` 诚实报 None，`timed_out` 仍为 True。
        """
        def _raise_lookup(*_a, **_kw):
            raise ProcessLookupError("simulated: already exited")

        monkeypatch.setattr(os, "killpg", _raise_lookup)
        spy = _SpawnSpy(monkeypatch)

        result = asyncio.run(_run_subprocess(
            "import time; time.sleep(0.5)", "python", 0.1, cwd=str(tmp_path)))

        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        assert result["signal"] is None, "进程自己正常退出时不得编造信号"
        proc = spy.proc
        assert proc.returncode == 0
        assert _wait_until_dead(proc.pid)

    def test_terminate_helper_on_already_exited_process_sends_nothing(self, monkeypatch):
        """进程已在超时那一刻自己结束：清理不再发信号，signal 按真实 returncode 归因。"""
        sent: list = []
        monkeypatch.setattr(os, "killpg", lambda *a, **kw: sent.append(a))

        class _Exited:
            pid = 424242
            returncode = -15

            async def wait(self):  # pragma: no cover - 不应被调用
                raise AssertionError("已退出的进程不应再被 wait")

        signal_num, notes = asyncio.run(_terminate_subprocess(_Exited(), reason="test"))
        assert signal_num == 15
        assert notes == []
        assert sent == [], "已退出的进程不应再被发信号"

    def test_terminate_helper_reports_but_does_not_raise_on_permission_error(
            self, monkeypatch, caplog):
        """杀不掉（PermissionError）：必须发声（warning）+ notes，但绝不抛异常，
        且不得编造 signal（进程未确认停止 → None）。"""
        def _deny(*_a, **_kw):
            raise PermissionError("simulated: not permitted")

        monkeypatch.setattr(os, "killpg", _deny)
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        # 宽限期缩短，避免为验证"两级升级都失败"白等真实的 2s+5s
        monkeypatch.setattr("app.services.subprocess_runner.SUBPROCESS_TERM_GRACE_S", 0.05)
        monkeypatch.setattr(
            "app.services.subprocess_runner.SUBPROCESS_KILL_REAP_TIMEOUT_S", 0.05)

        class _Stubborn:
            pid = 424243
            returncode = None

            async def wait(self):
                await asyncio.sleep(3600)   # 永远不停，逼出两次宽限超时

        async def _run():
            return await _terminate_subprocess(_Stubborn(), reason="test")

        with caplog.at_level("WARNING", logger="rebuild.subprocess_runner"):
            signal_num, notes = asyncio.run(_run())

        assert signal_num is None, "未确认停止 → 不得编造 signal"
        assert any("SIGTERM 失败" in n for n in notes)
        assert any("SIGKILL 失败" in n for n in notes)
        assert caplog.records, "清理失败必须发声（公理3：不静默）"

    def test_timeout_conclusion_survives_cleanup_notes(self, monkeypatch, tmp_path):
        """清理异常只追加到 stderr 说明，不得覆盖"这次调用超时了"的主结论。"""
        real_killpg = os.killpg          # 测试自身收尾要用真的
        monkeypatch.setattr(
            os, "killpg", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("nope")))
        monkeypatch.setattr(
            "app.services.subprocess_runner.SUBPROCESS_TERM_GRACE_S", 0.05)
        monkeypatch.setattr(
            "app.services.subprocess_runner.SUBPROCESS_KILL_REAP_TIMEOUT_S", 0.05)
        spy = _SpawnSpy(monkeypatch)

        result = asyncio.run(_run_subprocess(
            "sleep 30", "bash", 0.2, cwd=str(tmp_path), enforce_whitelist=False))

        assert result["timed_out"] is True
        assert "Timeout after" in result["stderr"]
        assert "清理异常" in result["stderr"]
        assert result["signal"] is None

        # 测试自身留下的真进程要收干净（本例故意让平台"杀不掉"）。
        # 这行收尾必须容忍"进程已经不在了"：`_release_subprocess_pipes` 里的
        # `transport.close()` 内部会用 `Popen.kill()`（走 `os.kill`，**绕过**本例
        # monkeypatch 掉的 `os.killpg`）强杀直接子进程，随后 `asyncio.run()` 收尾时把它
        # 回收 —— 于是 `os.getpgid(pid)` 可能直接抛 ProcessLookupError。这是本用例**自身
        # 收尾**的竞态，与被测代码无关：用重构前的原始实现逐字复现同样约 1/12 概率复发
        # （R21 拆分批次实测），属先前遗留的偶发失败，此处一并按"已经停了也算达成目的"处理。
        proc = spy.procs[0]
        try:
            real_killpg(os.getpgid(proc.pid), 9)
        except ProcessLookupError:
            pass
        assert _wait_until_dead(proc.pid)


# ══════════════════════════════════════════════════════════════════
# ⑤ 正常路径回归：行为不变
# ══════════════════════════════════════════════════════════════════

class TestNonTimeoutPathsUnchanged:

    def test_success_stdout_and_fields_unchanged(self, tmp_path):
        result = asyncio.run(_run_subprocess(
            "echo hi", "bash", 5, cwd=str(tmp_path), enforce_whitelist=False))
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "hi"
        assert result["stderr"] == ""
        assert result["timed_out"] is False
        assert result["signal"] is None
        assert result["blocked"] is False

    def test_nonzero_exit_is_not_reported_as_timeout(self, tmp_path):
        result = asyncio.run(_run_subprocess(
            "exit 3", "bash", 5, cwd=str(tmp_path), enforce_whitelist=False))
        assert result["exit_code"] == 3
        assert result["timed_out"] is False
        assert result["signal"] is None

    def test_whitelist_block_unchanged(self):
        result = asyncio.run(_run_subprocess("mvn -v", "bash", 5))
        assert result["blocked"] is True
        assert result["exit_code"] == 1
        assert result["timed_out"] is False
        assert result["signal"] is None

    def test_spawn_failure_is_not_reported_as_timeout(self, tmp_path):
        """进程根本没起来（cwd 不存在）：没有进程可清理，也不是超时。"""
        result = asyncio.run(_run_subprocess(
            "echo hi", "bash", 5, cwd=str(tmp_path / "missing"), enforce_whitelist=False))
        assert result["exit_code"] == -1
        assert result["timed_out"] is False
        assert result["signal"] is None
        assert result["stderr"]

    def test_workspace_provider_success_path_unchanged(self, tmp_path):
        provider = WorkspaceLocalExecutionProvider()
        result = asyncio.run(provider.execute("echo ok", language="bash", timeout=5,
                                              cwd=str(tmp_path)))
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "ok"
        assert result["timed_out"] is False
        assert result["risk_level"]


# ══════════════════════════════════════════════════════════════════
# signal 派生工具：只反映真实 returncode，不猜
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("returncode,expected", [
    (0, None), (3, None), (-15, 15), (-9, 9), (None, None),
])
def test_signal_from_returncode_is_honest(returncode, expected):
    assert _signal_from_returncode(returncode) == expected
