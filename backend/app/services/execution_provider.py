"""Execution provider seam — selects HOW code/commands run in the platform.

D-076 (2026-06-26): Code execution and AI coding agents are strictly separate paths.
  - ExecutionProvider handles subprocess / container execution ONLY.
  - AI coding agent invocation goes through CodingAgentAdapter (coding_agent_adapter.py).
  - ExecutionProvider must NEVER depend on OpenCode CLI availability.

R8: ContainerExecutionProvider wired via docker-py (Plan A, Q-R8-01 confirmed).
R9-5-6: RemoteSSHExecutionProvider added (D-093). Three-mode factory.

Modes (env `EXECUTION_MODE`, default "local"):
  - "local"     → LocalSubprocessExecutionProvider: host subprocess behind deny-list.
  - "container" → ContainerExecutionProvider: disposable, network-isolated container.
                  NEVER silently downgrades to host execution.
  - "remote"    → RemoteSSHExecutionProvider: paramiko SSH + SFTP (D-093).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("rebuild.execution_provider")


# ── Security boundaries (D-076: owned by ExecutionProvider, not opencode_adapter) ─

DENY_SUBSTRINGS = [
    "rm -rf", "rm -r", "sudo", "curl | sh", "wget | sh",
    "/dev/", "/proc/", "/sys/", "~/.ssh", "~/.gnupg",
    ".env", "id_rsa", "id_ed25519", "id_ecdsa",
    "/etc/passwd", "/etc/shadow", "/root/",
]

# R18-1 P1-06（D-033 缺口）：子串匹配无法覆盖变体，实测漏报 3 条——fork bomb
# `:(){ :|:&;}:;`、`chmod 777 /`、`chown -R root /`。改为"编译正则优先 + 子串兜底"。
# 吸收自 rebuild-archive/V10/原始代码/backend/app/sandbox/gvisor.py:28-41 的 DENY_LIST_COMMANDS
# 思路（正则化），但【只取本轮实测漏报的三条并适配收窄】，不整体复制：
#   - chmod：V10 原式 `chmod\s+.*777\s+/` 会把 `chmod 777 /srv/app/main.py` 也升为 L5，
#     与既有 L4 语义（test_r956_execution）冲突且属过度拦截 → 收窄为仅锚定根目录 `/`。
#   - 未吸收 V10 的 DENY_LIST_PATTERNS（`(root|etc|proc|sys|dev)` 宽匹配 / `credentials`）：
#     它会误杀 `npm run dev` 之类合法命令，属行为退化，见施工记录"吸收记录"。
DENY_REGEXES: list[re.Pattern] = [
    # fork bomb 及其变体：:(){ :|:&;}:;
    re.compile(r":\(\)\s*\{.*:\|:&.*\};*:", re.IGNORECASE),
    # 根目录整体放权：chmod [-R] 777 /（仅根，具体文件仍按 L4 处理）
    re.compile(r"\bchmod\s+(?:-\S+\s+)*777\s+/(?=[\s;&|]|$)", re.IGNORECASE),
    # 递归把绝对路径改归 root：chown -R root /...
    re.compile(r"\bchown\s+(?:-\S+\s+)*-R\s+(?:-\S+\s+)*root(?::\S+)?\s+/", re.IGNORECASE),
]

# R18-1 P1-01：环境变量【值】中的内嵌凭据 scheme://user[:password]@host
# （`[^/\s]+@` 贪婪到首个 `/` 之前的最后一个 `@`，避免口令自带 `@` 时残留片段）
_URL_CREDENTIAL_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s]+@")

ALLOWED_COMMANDS = ["python3", "python", "echo", "cat", "ls", "pwd", "which"]

# Commands that are high-risk but not in DENY_SUBSTRINGS (L4 soft-block)
_L4_PATTERNS = [
    "chmod", "chown", "chgrp", "crontab", "iptables", "ip6tables",
    "nftables", "nc -l", "ncat -l", "netcat -l", "mkfs", "fdisk",
    "dd if=", "kill -9", "killall", "systemctl", "service ",
    "useradd", "userdel", "passwd", "adduser",
]


def _strip_url_credentials(value: str) -> str:
    """R18-1 P1-01：剥离 URL 值中内嵌的 user[:password]@，保留 scheme/host/port/path。

    `postgresql://user:口令@localhost:5432/db` → `postgresql://localhost:5432/db`
    非 URL 的普通值原样返回（不误伤）。纯正则实现，不引 urlparse 异常分支
    （避免为 parse 失败写静默 except，公理3）。
    """
    return _URL_CREDENTIAL_RE.sub(r"\1", value, count=1)


def _match_deny_regex(code: str) -> str | None:
    """R18-1 P1-06：编译正则 DENY 检查（覆盖子串匹配漏报的变体）。命中返回模式串。"""
    for pat in DENY_REGEXES:
        if pat.search(code):
            return pat.pattern
    return None


def _check_dangerous(code: str) -> str | None:
    # R18-1 P1-06：正则检查优先于子串检查（子串漏报 fork bomb / chmod 777 / / chown -R root /）
    hit = _match_deny_regex(code)
    if hit:
        return f"Blocked dangerous pattern: {hit}"
    code_lower = code.lower()
    for pattern in DENY_SUBSTRINGS:
        if pattern.lower() in code_lower:
            return f"Blocked dangerous pattern: {pattern}"
    return None


def _classify_risk(code: str, language: str = "exec") -> str:
    """Classify execution risk level L0-L5 (R9-5-6 T8 / D-093).

    Returns the risk level string. Does NOT block — caller decides based on mode.
    """
    code_lower = code.lower()

    # L5: hard deny patterns (DENY_REGEXES 正则 + DENY_SUBSTRINGS 子串)
    if _match_deny_regex(code):
        return "L5"
    for p in DENY_SUBSTRINGS:
        if p.lower() in code_lower:
            return "L5"

    # L4: system-modification patterns
    for p in _L4_PATTERNS:
        if p.lower() in code_lower:
            return "L4"

    if language in ("python", "python3"):
        # Python: flag subprocess/os.system calls as L3
        if any(x in code_lower for x in ("subprocess", "os.system", "os.popen", "exec(")):
            return "L3"
        return "L1"

    # Shell/bash
    first_word = (code.strip().split() or [""])[0].split("/")[-1].lower()
    if first_word in {c.lower() for c in ALLOWED_COMMANDS}:
        return "L1"
    # Unknown command
    return "L3"


def _clean_env() -> dict:
    env = os.environ.copy()
    for key in list(env.keys()):
        if any(s in key.upper() for s in ["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"]):
            del env[key]
        else:
            # R18-1 P1-01：名不敏感的变量，其【值】里仍可能内嵌凭据
            # （DATABASE_URL=postgresql://user:口令@host 会原样传给子进程）→ 剥离 user:pass。
            env[key] = _strip_url_credentials(env[key])
    # WP-E (B-R17.2-P5-PATH): inherit the host PATH so installed toolchains
    # (sdkman java, nvm node, dotnet, go, mvn, npm …) are visible to P5 build/verify
    # commands. Previously PATH was pinned to /usr/local/bin:/usr/bin:/bin, which hid
    #信创 toolchains → mvn/npm/go returned "command not found" (127) → false
    # validation_failed. PATH is not a secret; sensitive KEY/TOKEN/SECRET/PASSWORD/
    # CREDENTIAL vars are still stripped above. Standard system dirs are guaranteed to
    # be present even if the host PATH is unusual/empty.
    host_path = os.environ.get("PATH", "")
    std_dirs = ["/usr/local/bin", "/usr/bin", "/bin"]
    parts = [p for p in host_path.split(os.pathsep) if p]
    for d in std_dirs:
        if d not in parts:
            parts.append(d)
    env["PATH"] = os.pathsep.join(parts)
    return env


class ExecutionProvider(Protocol):
    """Contract every execution backend implements (R9-5-6 T3: aligned signature)."""

    name: str

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        """Run code/command and return the standard ExecutionResult dict.

        Result keys: exit_code, stdout, stderr, elapsed_ms, provider,
        execution_mode, fallback, blocked, risk_level, audited.
        Never raises for ordinary execution failures.
        """
        ...


class LocalSubprocessExecutionProvider:
    """Host-subprocess execution behind the deny-list (current default).

    D-076: Does NOT depend on OpenCode CLI. Subprocess is always available
    regardless of whether any AI coding agent is installed.
    """

    name = "local_subprocess"

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        import time
        start = time.monotonic()

        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "security_block",
                "execution_mode": "local", "fallback": True, "blocked": True,
                "risk_level": "L5", "audited": False,
                # D-034（R18-1 P1-02）：L5 不再静默 block —— 上报 gate_required，
                # 由调用侧（stage_handlers）创建可裁决的用户 Gate。
                "gate_required": True,
            }

        risk = _classify_risk(code, language)
        result = await _run_subprocess(code, language, timeout, cwd=cwd)
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        result["execution_mode"] = "local"
        result["risk_level"] = risk
        result["audited"] = False
        return result


async def _run_subprocess(code: str, language: str, timeout: int,
                         cwd: str | None = None,
                         enforce_whitelist: bool = True) -> dict:
    """Execute via python3 -c or bash -c in a clean environment.

    R9-3A: Accepts optional cwd to bind execution to project workspace (WP-A1.1).
    R12-10: enforce_whitelist=False allows platform-internal P5 commands
    (mvn/go/npm/cargo/make/cmake) while keeping DENY_SUBSTRINGS L5 check.
    """
    clean_env = _clean_env()

    if language in ("python", "python3"):
        cmd = ["python3", "-c", code]
    elif language in ("bash", "sh", "shell"):
        if enforce_whitelist:
            first_word = (code.strip().split() or [""])[0].split("/")[-1]
            if first_word and first_word not in ALLOWED_COMMANDS:
                return {
                    "exit_code": 1, "stdout": "",
                    "stderr": f"命令不在允许列表中: {first_word}",
                    "provider": "local_subprocess", "fallback": False, "blocked": True,
                }
        cmd = ["bash", "-c", code]
    else:
        cmd = ["python3", "-c", code]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_env,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "exit_code": proc.returncode or 0,
            "stdout": stdout.decode("utf-8", errors="replace")[:65536],
            "stderr": stderr.decode("utf-8", errors="replace")[:65536],
            "provider": "local_subprocess",
            "fallback": False,
            "blocked": False,
        }
    except asyncio.TimeoutError:
        return {
            "exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s",
            "provider": "local_subprocess", "fallback": False, "blocked": False,
        }
    except Exception as e:
        return {
            "exit_code": -1, "stdout": "", "stderr": str(e),
            "provider": "local_subprocess", "fallback": False, "blocked": False,
        }


class ContainerExecutionProvider:
    """R8: Execute inside a disposable isolated container via docker-py.

    Uses the execution-sandbox image built in deploy/execution-sandbox/.
    Container runs with: no network, read-only root, non-root, cap_drop ALL.
    NEVER silently downgrades to host execution.
    """

    name = "container_sandbox"

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None) -> dict:
        import time
        start = time.time()

        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "security_block",
                "execution_mode": "container", "fallback": False, "blocked": True,
                "risk_level": "L5", "audited": False,
                "gate_required": True,   # D-034（R18-1 P1-02）：L5 须可裁决，不静默 block
            }

        # Write code to a temp file that will be mounted into the container.
        # The sandbox runs as non-root uid 10002 which does not own this host
        # tempdir, so make the dir traversable and the file world-readable.
        tmpdir = tempfile.mkdtemp(prefix="rb-exec-")
        code_file = Path(tmpdir) / ("code.py" if language in ("python", "python3") else "script.sh")
        code_file.write_text(code, encoding="utf-8")
        os.chmod(tmpdir, 0o755)
        os.chmod(code_file, 0o644)

        is_python = language in ("python", "python3")
        cmd = ["python3", "/workspace/code.py"] if is_python else ["bash", "/workspace/script.sh"]

        try:
            import docker
            client = docker.from_env()
            container = client.containers.run(
                image="rebuild-execution-sandbox:local",
                command=cmd,
                volumes={tmpdir: {"bind": "/workspace", "mode": "ro"}},
                network_mode="none",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="256m",
                pids_limit=64,
                user="10002",
                detach=True,
                # NOTE: no auto-remove here — we must wait() + read logs() first;
                # the finally block force-removes. remove=True races the log read (409).
            )
            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")[-8000:]
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")[-4000:]
            except Exception:
                container.kill()
                exit_code = -1
                stdout = ""
                stderr = f"Container execution timed out after {timeout}s"
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    # advisory：best-effort 清理容器；执行结果已在上方捕获，清理失败不影响返回值。
                    logger.debug("容器清理 remove 失败（best-effort）", exc_info=True)
        except Exception as e:
            exit_code = -1
            stdout = ""
            stderr = f"Container execution error: {e}. Is docker running and rebuild-execution-sandbox:local built?"
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                # advisory：best-effort 清理临时目录；清理失败不影响执行结果，仅可能残留临时文件。
                logger.debug("临时目录清理 rmtree 失败（best-effort）tmpdir=%s", tmpdir, exc_info=True)

        elapsed = int((time.time() - start) * 1000)
        risk = _classify_risk(code, language)
        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_ms": elapsed,
            "provider": "container_sandbox",
            "execution_mode": "container",
            "fallback": False,
            "blocked": False,
            "risk_level": risk,
            "audited": False,
        }


class WorkspaceLocalExecutionProvider:
    """R12-8: Workspace-bound local execution for P5 verification commands.

    Unlike LocalSubprocessExecutionProvider (which enforces ALLOWED_COMMANDS
    whitelist suitable only for tiny utility snippets), this provider runs
    build/test/verify commands directly in the project workspace with full
    toolchain access (mvn, npm, go, cargo, make, cmake, node, python3, etc).

    Security model (D-088② / D-076):
      - Still checks DENY_SUBSTRINGS (L5 hard block: sudo, curl|sh, rm -rf …).
      - No ALLOWED_COMMANDS whitelist — P5 is platform-internal (trusted),
        not user-supplied untrusted code.
      - cwd is always the project workspace root (no escape).
      - Risk is classified and audited, not silently elevated.

    This solves B-P5-CONTAINER-CWD: ContainerExecutionProvider lacks cwd and
    read-only-only mounts, which makes it architecturally unable to run real
    project builds. For P5 trusted workspace execution, local with DENY check
    + workspace-bound cwd is the correct seam.
    """

    name = "workspace_local"

    async def execute(self, code: str, language: str = "bash",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        import time
        start = time.time()

        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "workspace_local_security_block",
                "execution_mode": "workspace_local", "fallback": True, "blocked": True,
                "risk_level": "L5", "audited": False,
                "gate_required": True,   # D-034（R18-1 P1-02）：L5 须可裁决，不静默 block
            }

        risk = _classify_risk(code, language)
        result = await _run_subprocess(code, language, timeout, cwd=cwd,
                                        enforce_whitelist=False)
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        result["execution_mode"] = "workspace_local"
        result["risk_level"] = risk
        result["audited"] = False
        return result


def get_execution_provider(
    mode: str | None = None,
    remote_host_id: str | None = None,
    db=None,
    *,
    trust_on_first_use: bool = False,
) -> "ExecutionProvider":
    """Return the execution provider for the given mode.

    Priority: explicit mode arg > EXECUTION_MODE env var > "local".
    Modes:
      - "local"           → LocalSubprocessExecutionProvider (ALLOWED_COMMANDS whitelist)
      - "workspace_local" → WorkspaceLocalExecutionProvider (workspace-bound, no whitelist, DENY check)
      - "container"       → ContainerExecutionProvider (docker sandbox, read-only)
      - "remote"          → RemoteSSHExecutionProvider (paramiko SSH + SFTP)

    trust_on_first_use: for the remote mode, accept unknown host keys on first
    connection and persist the fingerprint (TOFU, D-093).  Default False =
    reject unknown hosts.
    """
    resolved_mode = (mode or os.environ.get("EXECUTION_MODE") or "local").strip().lower()
    if resolved_mode == "container":
        return ContainerExecutionProvider()
    if resolved_mode == "workspace_local":
        return WorkspaceLocalExecutionProvider()
    if resolved_mode == "remote":
        if remote_host_id is None or db is None:
            raise ValueError(
                "get_execution_provider(mode='remote') requires remote_host_id and db"
            )
        from app.services.remote_executor import RemoteSSHExecutionProvider
        from app.services.remote_service import RemoteService
        host = RemoteService(db).get(remote_host_id)
        if host is None:
            raise ValueError(f"RemoteHost not found: {remote_host_id}")
        return RemoteSSHExecutionProvider(host, db, trust_on_first_use=trust_on_first_use)
    return LocalSubprocessExecutionProvider()
