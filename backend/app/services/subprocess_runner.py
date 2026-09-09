"""子进程执行原语 —— 起进程、等结果、**保证不留孤儿进程**（R21）。

从 `execution_provider.py` 原地抽出（R21 卫生批次）。抽取动因两条，缺一不可：

  ① **去重（首要）**：`git_service.run_git_command` 有与 `_run_subprocess` 一模一样的孤儿
     进程缺陷 —— `asyncio.wait_for(proc.communicate(), timeout=…)` 超时取消的只是协程，
     子进程本身继续跑。真实影响：`git clone` 大仓库超时后留下一个持续下载的孤儿进程，
     占网络和磁盘，而平台的项目接入流程（D-058 Git 接入）会真实走到这条路径。
     修法必须只有**一份**：本仓刚因"脱敏模式表有两份副本悄悄漂移"
     （`B-R20-REDACT-THREE-IMPLS`）踩过复制粘贴的坑，不再在第二处重写 SIGTERM/SIGKILL。
  ② **行数**：`execution_provider.py` 因 R21 修复从 757 涨到 905 行，越过 800 行上限
     （编码规范：200-400 行典型 / 800 行上限）。

**职责边界**：本模块只管进程生命周期（起、等、终止、收尾），**不含任何安全策略** ——
DENY 名单、命令白名单、环境清洗、风险分级一律留在 `execution_provider.py`。调用方自己
拼好 argv 与 env 后交由这里执行。

**公开接口**：`run_subprocess_command()` + `SubprocessOutcome` + 两个超时清理常量。
清理原语（`_signal_from_returncode` / `_signal_subprocess` / `_release_subprocess_pipes`
/ `_terminate_subprocess`）是本模块实现细节，调用方不需要、也不应直接使用。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal as signal_mod
from dataclasses import dataclass

logger = logging.getLogger("rebuild.subprocess_runner")


# ── 子进程超时清理常量（R21：超时后必须真正终止，且等到它确实停止）──────────
# 温和终止（SIGTERM）后留给子进程自己收尾的宽限期（秒）。需短到不把"清理"变成
# 第二次挂起，又长到让 bash / 构建工具跑完自己的 trap 收尾。
SUBPROCESS_TERM_GRACE_S = 2.0
# SIGKILL 之后确认进程停止（wait）的上限（秒）。内核强杀后 wait 正常会立即返回；
# 设上限只为不让"清理"本身无界挂起（不可中断 IO 等极端情况），到期如实上报未确认。
SUBPROCESS_KILL_REAP_TIMEOUT_S = 5.0
# 是否具备"按进程组清理"的能力（POSIX 才有 setsid/killpg/getpgid）。
# 有 → 子进程以 start_new_session 起在独立进程组，超时按整组终止，`bash -c` 拉起的
# 孙进程（mvn/dotnet 的 fork、`cmd &` 后台任务、`git clone` 的 remote helper）不会变孤儿。
_PROCESS_GROUP_KILL_SUPPORTED = hasattr(os, "killpg") and hasattr(os, "getpgid") and hasattr(os, "setsid")

# 单流输出上限（解码后字符数）。抽取前 `_run_subprocess` 与 `run_git_command` 各自写死
# 65536，取值本就一致 ⇒ 收敛为一个常量，避免两处日后各自漂移。
SUBPROCESS_OUTPUT_MAX_CHARS = 65536


@dataclass(frozen=True)
class SubprocessOutcome:
    """一次子进程执行的**事实**（不可变）。调用方据此拼自己的返回契约。

    `timed_out` / `exit_code` / `signal` 三个事实各自独立、不互相嵌套依赖
    （R21 循环卫生②）：超时时 `exit_code` 仍是既有 -1 语义，`signal` 只从真实
    returncode 派生、不编造。
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    signal: int | None


def _signal_from_returncode(returncode: int | None) -> int | None:
    """POSIX 约定：returncode 为负数 `-N` ⇒ 被信号 N 终止；`>=0` ⇒ 正常退出（无信号可归因）。

    returncode 为 None ⇒ 进程尚未被确认收尾 ⇒ **不编造信号**，如实报 None。
    """
    if returncode is None or returncode >= 0:
        return None
    return -returncode


def _signal_subprocess(proc, sig: int, *, process_group: bool) -> str:
    """给子进程（或它整个进程组）发一个信号。

    返回 "sent" / "gone"（进程已自行退出，属竞态、不是错误）/ "error: ..."（真的失败，
    如 PermissionError 杀不掉）。**绝不抛异常**——清理动作出错不得把"这次调用超时了"
    这个主结论顶掉（发声由调用方 logger.warning 负责，公理3）。
    """
    try:
        if process_group:
            # 子进程以 start_new_session 起在自己的进程组（pgid == pid），getpgid 同时
            # 起到"进程是否还在"的校验作用：已回收则抛 ProcessLookupError → "gone"。
            os.killpg(os.getpgid(proc.pid), sig)
        else:
            proc.send_signal(sig)
        return "sent"
    except (ProcessLookupError, ChildProcessError):
        return "gone"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


def _release_subprocess_pipes(proc) -> None:
    """`communicate()` 被取消后，stdout/stderr 管道的 transport 仍挂在事件循环上，靠
    EOF 回调/GC 兜底才释放。平台是长期运行进程 ⇒ 主动关闭，及时释放管道 FD
    （与"真正终止进程"同一诉求：清理要做完，不留半截）。best-effort，不影响主结论。
    """
    transport = getattr(proc, "_transport", None)
    if transport is None:
        return
    try:
        transport.close()
    except Exception:
        logger.debug("子进程 transport 关闭失败（best-effort）pid=%s",
                     getattr(proc, "pid", None), exc_info=True)


async def _terminate_subprocess(proc, *, reason: str) -> tuple[int | None, list[str]]:
    """真正终止一个还在跑的子进程，并**等到它确实停止**（R21 实质缺陷修复）。

    此前超时分支只取消 `communicate()` 协程，子进程本身继续跑 —— 一次超时的
    `dotnet build` / `mvn` / 测试套件 / `git clone` 会变成永久运行的孤儿进程，持续占
    CPU/内存/网络/文件锁，平台长期运行不断累积。修法：

      ① SIGTERM（温和），给 `SUBPROCESS_TERM_GRACE_S` 秒宽限期等它自己收尾；
      ② 宽限期内没退出 → SIGKILL，并再次等待，直到确认真的结束；
      ③ 有 killpg 能力时按**进程组**发信号，`bash -c` / `git remote-ext` 拉起的孙进程
         一并清掉。

    返回 `(signal_num, notes)`：
      - `signal_num` 从最终的真实 returncode 派生（被 TERM 杀 → 15；被 KILL 杀 → 9；
        收到 SIGTERM 后自己正常退出 → None）。**不编造**。
      - `notes` 为需要如实上报的清理异常（同时已 logger.warning）；正常清理为空。
    本函数从不抛异常。
    """
    if proc.returncode is not None:
        # 竞态：进程正好在超时那一刻自己结束了 → 无需终止，按真实 returncode 归因。
        _release_subprocess_pipes(proc)
        return _signal_from_returncode(proc.returncode), []

    notes: list[str] = []
    by_group = _PROCESS_GROUP_KILL_SUPPORTED
    if not by_group:
        notes.append("本平台无 setsid/killpg，只能终止直接子进程，其孙进程可能残留")
        logger.warning("子进程清理降级为单进程终止（无 killpg 能力）：%s", reason)

    term = _signal_subprocess(proc, signal_mod.SIGTERM, process_group=by_group)
    if term.startswith("error"):
        notes.append(f"SIGTERM 失败：{term}")
        logger.warning("子进程 SIGTERM 失败（%s）：%s pid=%s", reason, term, proc.pid)

    try:
        await asyncio.wait_for(proc.wait(), timeout=SUBPROCESS_TERM_GRACE_S)
    except asyncio.TimeoutError:
        # 宽限期内没停 → 强杀，并再等它真正结束（不是"发个信号就返回"）。
        kill = _signal_subprocess(proc, signal_mod.SIGKILL, process_group=by_group)
        if kill.startswith("error"):
            notes.append(f"SIGKILL 失败：{kill}")
            logger.warning("子进程 SIGKILL 失败（%s）：%s pid=%s", reason, kill, proc.pid)
        try:
            await asyncio.wait_for(proc.wait(), timeout=SUBPROCESS_KILL_REAP_TIMEOUT_S)
        except asyncio.TimeoutError:
            notes.append(
                f"SIGKILL 后 {SUBPROCESS_KILL_REAP_TIMEOUT_S}s 内仍未确认子进程停止（可能残留）")
            logger.warning("子进程 SIGKILL 后未确认停止（%s）pid=%s", reason, proc.pid)
    except Exception as e:
        # wait() 本身出错（极端情况）：发声，但不覆盖主结论。
        notes.append(f"等待子进程停止时出错：{type(e).__name__}: {e}")
        logger.warning("等待子进程停止出错（%s）pid=%s", reason, proc.pid, exc_info=True)

    # `communicate()` 被取消后管道 transport 仍在，主动释放（见 _release_subprocess_pipes）。
    _release_subprocess_pipes(proc)

    return _signal_from_returncode(proc.returncode), notes


def _with_cleanup_notes(stderr: str, notes: list[str]) -> str:
    """把清理异常如实追加到 stderr 说明里 —— 只**追加**，不覆盖主结论（公理3）。"""
    if not notes:
        return stderr
    return stderr + "（子进程清理异常：" + "；".join(notes) + "）"


async def run_subprocess_command(cmd: list[str], *, timeout: float,
                                 cwd: str | None = None,
                                 env: dict | None = None) -> SubprocessOutcome:
    """执行 argv 并采集 stdout/stderr；每条退出路径都**真正终止子进程并等它停稳**。

    行为契约（`execution_provider._run_subprocess` 与 `git_service.run_git_command`
    共用这一份，不再各写一套）：

      - 正常结束 → 真实 `exit_code` / 输出 / `signal`（POSIX 负 returncode 派生）。
      - **超时** → SIGTERM + 宽限期 → SIGKILL（有能力时按整个进程组），确认停止后返回
        `timed_out=True` / `exit_code=-1` / `stderr="Timeout after {timeout}s"`
        （清理异常如实追加，见 `_with_cleanup_notes`）。
      - **取消**（`asyncio.CancelledError`）→ 同步对进程组发 SIGKILL、释放管道，然后
        **继续向上传播**取消（取消语义不吞；不在取消处理里 await，await 会被再次取消打断）。
      - **其它异常** → 同样意味着我们再也不会去收这个进程 ⇒ 一并清理，返回
        `exit_code=-1` / `stderr=str(e)` / `timed_out=False`。
      - **进程起不来**（命令不存在 / cwd 不存在 …）→ 异常**原样抛给调用方**：此时没有
        进程需要清理，而错误措辞属调用方策略（git 侧要报 "git command not found"），
        本模块不代为决定。

    `env=None` 表示继承当前进程环境；是否清洗环境由调用方决定
    （见 `execution_provider._clean_env`，git 侧需要宿主 PATH/HOME 才能跑）。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        # R21：独立会话/进程组，使超时后能按整组清理（孙进程也在组内）。
        start_new_session=_PROCESS_GROUP_KILL_SUPPORTED,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # 超时分支：timed_out 必须独立、真实报 True。R21：此处**主动终止子进程并等它
        # 确实停止**（原实现只取消 communicate()，子进程会继续跑成孤儿）。
        # signal 如实反映最终归因（SIGTERM→15 / SIGKILL→9 / 收到 TERM 后自己正常退出→None）；
        # exit_code 保留既有 -1 语义（不是 0，也不冒充某个信号退出码）。
        signal_num, notes = await _terminate_subprocess(proc, reason=f"timeout after {timeout}s")
        return SubprocessOutcome(
            exit_code=-1, stdout="",
            stderr=_with_cleanup_notes(f"Timeout after {timeout}s", notes),
            timed_out=True, signal=signal_num,
        )
    except asyncio.CancelledError:
        _signal_subprocess(proc, signal_mod.SIGKILL,
                           process_group=_PROCESS_GROUP_KILL_SUPPORTED)
        _release_subprocess_pipes(proc)
        logger.warning("子进程执行被取消，已对进程组发送 SIGKILL pid=%s", proc.pid)
        raise
    except Exception as e:
        signal_num, notes = await _terminate_subprocess(proc, reason=f"error: {type(e).__name__}")
        return SubprocessOutcome(
            exit_code=-1, stdout="", stderr=_with_cleanup_notes(str(e), notes),
            timed_out=False, signal=signal_num,
        )

    returncode = proc.returncode or 0
    return SubprocessOutcome(
        exit_code=returncode,
        stdout=stdout.decode("utf-8", errors="replace")[:SUBPROCESS_OUTPUT_MAX_CHARS],
        stderr=stderr.decode("utf-8", errors="replace")[:SUBPROCESS_OUTPUT_MAX_CHARS],
        timed_out=False,
        signal=_signal_from_returncode(returncode),
    )
