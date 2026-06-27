"""Execution provider seam — selects HOW code/commands run in the platform.

D-076 (2026-06-26): Code execution and AI coding agents are strictly separate paths.
  - ExecutionProvider handles subprocess / container execution ONLY.
  - AI coding agent invocation goes through CodingAgentAdapter (coding_agent_adapter.py).
  - ExecutionProvider must NEVER depend on OpenCode CLI availability.

R8: ContainerExecutionProvider wired via docker-py (Plan A, Q-R8-01 confirmed).

Modes (env `EXECUTION_MODE`, default "local"):
  - "local"     → LocalSubprocessExecutionProvider: host subprocess behind deny-list.
  - "container" → ContainerExecutionProvider: disposable, network-isolated container.
                  NEVER silently downgrades to host execution.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Protocol


# ── Security boundaries (D-076: owned by ExecutionProvider, not opencode_adapter) ─

DENY_SUBSTRINGS = [
    "rm -rf", "rm -r", "sudo", "curl | sh", "wget | sh",
    "/dev/", "/proc/", "/sys/", "~/.ssh", "~/.gnupg",
    ".env", "id_rsa", "id_ed25519", "id_ecdsa",
    "/etc/passwd", "/etc/shadow", "/root/",
]

ALLOWED_COMMANDS = ["python3", "python", "echo", "cat", "ls", "pwd", "which"]


def _check_dangerous(code: str) -> str | None:
    code_lower = code.lower()
    for pattern in DENY_SUBSTRINGS:
        if pattern.lower() in code_lower:
            return f"Blocked dangerous pattern: {pattern}"
    return None


def _clean_env() -> dict:
    env = os.environ.copy()
    for key in list(env.keys()):
        if any(s in key.upper() for s in ["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"]):
            del env[key]
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    return env


class ExecutionProvider(Protocol):
    """Contract every execution backend implements."""

    name: str

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None) -> dict:
        """Run code and return the standard ExecutionResult dict.

        Result keys: exit_code, stdout, stderr, elapsed_ms, provider, fallback,
        blocked. Never raises for ordinary execution failures.
        """
        ...


class LocalSubprocessExecutionProvider:
    """Host-subprocess execution behind the deny-list (current default).

    D-076: Does NOT depend on OpenCode CLI. Subprocess is always available
    regardless of whether any AI coding agent is installed.
    """

    name = "local_subprocess"

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None) -> dict:
        import time
        start = time.monotonic()

        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "security_block",
                "execution_mode": "local", "fallback": True, "blocked": True,
            }

        result = await _run_subprocess(code, language, timeout)
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        result["execution_mode"] = "local"
        return result


async def _run_subprocess(code: str, language: str, timeout: int) -> dict:
    """Execute via python3 -c or bash -c in a clean environment."""
    clean_env = _clean_env()

    if language in ("python", "python3"):
        cmd = ["python3", "-c", code]
    elif language in ("bash", "sh", "shell"):
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
                    pass
        except Exception as e:
            exit_code = -1
            stdout = ""
            stderr = f"Container execution error: {e}. Is docker running and rebuild-execution-sandbox:local built?"
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                pass

        elapsed = int((time.time() - start) * 1000)
        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_ms": elapsed,
            "provider": "container_sandbox",
            "execution_mode": "container",
            "fallback": False,
            "blocked": False,
        }


def get_execution_provider() -> ExecutionProvider:
    """Return the execution provider selected by EXECUTION_MODE (default local)."""
    mode = (os.environ.get("EXECUTION_MODE") or "local").strip().lower()
    if mode == "container":
        return ContainerExecutionProvider()
    return LocalSubprocessExecutionProvider()
