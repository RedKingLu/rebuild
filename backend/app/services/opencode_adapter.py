"""OpenCode adapter — CLI subprocess execution with fallback shell executor.

Security boundaries per CL-R7-3-3:
- cwd locked to sandbox directory
- timeout enforced
- DENY_SUBSTRINGS for dangerous commands
- env vars cleared of secrets
- stdout/stderr truncated
"""

import asyncio
import os
import shutil
import time


SANDBOX_BASE = os.path.join(os.getcwd(), ".data", "execution-sandbox")

DENY_SUBSTRINGS = [
    "rm -rf", "rm -r", "sudo", "curl | sh", "wget | sh",
    "/dev/", "/proc/", "/sys/", "~/.ssh", "~/.gnupg",
    ".env", "id_rsa", "id_ed25519", "id_ecdsa",
    "/etc/passwd", "/etc/shadow", "/root/",
]

ALLOWED_COMMANDS = ["python3", "python", "echo", "cat", "ls", "pwd", "which"]


_OPENCODE_BIN = None


def _find_opencode() -> str | None:
    """Locate the opencode binary (npm global or PATH)."""
    global _OPENCODE_BIN
    if _OPENCODE_BIN:
        return _OPENCODE_BIN
    # Try PATH first
    found = shutil.which("opencode")
    if found:
        _OPENCODE_BIN = found
        return found
    # Try npm global install path
    import subprocess
    try:
        result = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=10)
        npm_root = result.stdout.strip()
        candidate = os.path.join(npm_root, "opencode-ai", "bin", "opencode.exe")
        if os.path.isfile(candidate):
            _OPENCODE_BIN = candidate
            return candidate
    except Exception:
        pass
    return None


def is_opencode_available() -> bool:
    """Check if opencode CLI is installed."""
    return _find_opencode() is not None


def _check_dangerous(code: str) -> str | None:
    """Return denial reason if code contains dangerous patterns, else None."""
    code_lower = code.lower()
    for pattern in DENY_SUBSTRINGS:
        if pattern.lower() in code_lower:
            return f"Blocked dangerous pattern: {pattern}"
    return None


def _check_command_allowlist(cmd: list[str]) -> str | None:
    """Check if the first command is in the allowlist."""
    if not cmd:
        return "Empty command"
    base = os.path.basename(cmd[0])
    if base not in ALLOWED_COMMANDS:
        return f"Command not in allowlist: {base}"
    return None


async def execute(code: str, language: str = "python",
                  timeout: int = 30, cwd: str = None,
                  model: str = None) -> dict:
    """Execute code via opencode CLI or fallback shell executor.

    Args:
        code: The code to execute
        language: 'python' or 'bash'
        timeout: Timeout in seconds
        cwd: Working directory (sandboxed)
        model: Model name to pass to opencode (e.g. 'deepseek-chat')

    Returns dict with: exit_code, stdout, stderr, elapsed_ms, provider, fallback
    """
    # Security check
    denial = _check_dangerous(code)
    if denial:
        return {
            "exit_code": 1, "stdout": "", "stderr": denial,
            "elapsed_ms": 0, "provider": "security_block",
            "fallback": True, "blocked": True,
        }

    # Ensure sandbox directory
    execution_id = _exec_id()
    sandbox_dir = cwd or os.path.join(SANDBOX_BASE, execution_id)
    os.makedirs(sandbox_dir, exist_ok=True)

    # Clean environment
    clean_env = _clean_env()

    start = time.monotonic()

    if is_opencode_available():
        result = await _run_opencode(code, language, timeout, sandbox_dir, clean_env, model)
    else:
        result = await _run_fallback(code, language, timeout, sandbox_dir, clean_env)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    result["elapsed_ms"] = elapsed_ms
    return result


async def _run_opencode(code: str, language: str, timeout: int,
                        cwd: str, env: dict, model: str = None) -> dict:
    """Execute via opencode CLI.

    Uses `opencode run --command` for direct command execution,
    or `opencode run <message>` with piped code for AI-assisted execution.
    """
    bin_path = _find_opencode()
    if not bin_path:
        return await _run_fallback(code, language, timeout, cwd, env)

    # Build command: use --command for direct shell execution
    if language == "bash" or language == "sh":
        cmd_str = code
    else:
        cmd_str = f"python3 -c {_shell_quote(code)}"

    args = [bin_path, "run", "--command", cmd_str, "--format", "json"]
    if model:
        args.extend(["-m", model])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
        rc = proc.returncode or 0
        if rc != 0:
            # OpenCode CLI present but the invocation failed (e.g. not logged in,
            # unsupported flag, model not configured). Fall back to the sandboxed
            # shell executor so the code still runs, and report it honestly.
            fb = await _run_fallback(code, language, timeout, cwd, env)
            fb["opencode_error"] = stderr.decode("utf-8", errors="replace")[:512]
            return fb
        return {
            "exit_code": rc,
            "stdout": stdout.decode("utf-8", errors="replace")[:65536],
            "stderr": stderr.decode("utf-8", errors="replace")[:65536],
            "provider": "opencode",
            "fallback": False,
            "blocked": False,
        }
    except asyncio.TimeoutError:
        return {
            "exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s",
            "provider": "opencode", "fallback": False, "blocked": False,
        }
    except Exception as e:
        return await _run_fallback(code, language, timeout, cwd, env)


def _shell_quote(s: str) -> str:
    """Safely quote a string for shell -c usage."""
    import shlex
    return shlex.quote(s)


async def _run_fallback(code: str, language: str, timeout: int,
                        cwd: str, env: dict) -> dict:
    """Execute via fallback shell executor (python3 -c or bash -c)."""
    if language == "python" or language == "python3":
        cmd = ["python3", "-c", code]
    elif language == "bash" or language == "sh":
        cmd = ["bash", "-c", code]
    else:
        cmd = ["python3", "-c", code]

    # Allowlist check for fallback
    denial = _check_command_allowlist(cmd)
    if denial:
        return {
            "exit_code": 1, "stdout": "", "stderr": denial,
            "provider": "fallback_shell", "fallback": True, "blocked": True,
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
        return {
            "exit_code": proc.returncode or 0,
            "stdout": stdout.decode("utf-8", errors="replace")[:65536],
            "stderr": stderr.decode("utf-8", errors="replace")[:65536],
            "provider": "fallback_shell",
            "fallback": True,
            "blocked": False,
        }
    except asyncio.TimeoutError:
        return {
            "exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s",
            "provider": "fallback_shell", "fallback": True, "blocked": False,
        }
    except Exception as e:
        return {
            "exit_code": -1, "stdout": "", "stderr": str(e),
            "provider": "fallback_shell", "fallback": True, "blocked": False,
        }


def _exec_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def _clean_env() -> dict:
    """Return a clean environment with secret env vars removed."""
    env = os.environ.copy()
    # Remove sensitive vars
    for key in list(env.keys()):
        if any(s in key.upper() for s in ["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"]):
            del env[key]
    # Ensure basic PATH
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    return env
