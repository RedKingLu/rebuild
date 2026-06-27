"""AI Coding Agent adapter — interface for external AI coding agents.

D-076 (2026-06-26): OpenCode/qcode are AI coding agents that accept natural-language
tasks, NOT shell wrappers for running subprocess commands.

OpenCodeCLIAdapter now implements real invocation via `opencode run <task>`.
Credentials are passed only as env vars to the subprocess (never logged/stored in plain text).
LangGraph-mediated review agent interception is deferred to R11 (D-078).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Protocol

# ── Protocol ─────────────────────────────────────────────────────────────

class CodingAgentAdapter(Protocol):
    """Interface every AI coding agent adapter implements."""

    agent_type: str

    def is_available(self) -> bool: ...

    async def invoke_coding_task(
        self,
        task: str,
        context_path: str,
        config: dict | None = None,
    ) -> dict:
        """Returns dict with: status, summary, changed_files, diff_ref, issues, raw_output."""
        ...


# ── Helpers ───────────────────────────────────────────────────────────────

# Providers that use the MaaS/DeepSeek-compatible base URL
_DEFAULT_BASE_URL = "http://maas.icompify.com:32788/v1"
_DEFAULT_MODEL = "openai/deepseek-v4-flash"
_FREE_MODEL = "opencode/deepseek-v4-flash-free"  # no API key required
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFJ]")
_OUTPUT_CAP = 32_768   # 32 KB cap on raw_output stored in response


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _build_opencode_env(config: dict) -> dict:
    """Build subprocess env: inject OPENAI_API_KEY + OPENAI_BASE_URL, strip LLM_API_KEY.

    Key is never returned or logged — it only lives in the subprocess env dict
    for the duration of the opencode run.
    """
    env = os.environ.copy()
    # Prefer key from platform env (set by operator, not from config JSON)
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = (
        config.get("base_url")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or _DEFAULT_BASE_URL
    )
    if api_key:
        env["OPENAI_API_KEY"] = api_key
        env["OPENAI_BASE_URL"] = base_url
    # Remove the raw platform key so opencode doesn't forward it in logs
    env.pop("LLM_API_KEY", None)
    return env


def _pick_model(config: dict, has_key: bool) -> str:
    """Return the model string to pass to opencode -m."""
    if config.get("model"):
        return config["model"]
    return _DEFAULT_MODEL if has_key else _FREE_MODEL


def _extract_summary(text: str) -> str:
    """Extract last non-empty line as summary (opencode prints result at end)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Skip lines that look like progress spinners or tool status
    skip_prefixes = ("●", "○", "✓", "✗", "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏", "│", "┌", "└", "─")
    result_lines = [l for l in lines if not any(l.startswith(p) for p in skip_prefixes)]
    if result_lines:
        return result_lines[-1][:256]
    return lines[-1][:256] if lines else ""


async def _git_changed_files(path: str) -> list[str]:
    """Return list of files modified/added/deleted since last commit (or all staged)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", "HEAD",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        tracked = [l.strip() for l in stdout.decode().splitlines() if l.strip()]

        # Also include untracked new files
        proc2 = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
        untracked = [l.strip() for l in stdout2.decode().splitlines() if l.strip()]

        return list(dict.fromkeys(tracked + untracked))  # preserve order, dedup
    except Exception:
        return []


# ── OpenCode CLI adapter ──────────────────────────────────────────────────

def is_opencode_available() -> bool:
    return shutil.which("opencode") is not None


class OpenCodeCLIAdapter:
    """Adapter for the OpenCode AI coding agent (opencode CLI).

    Invokes `opencode run <task> --dir <context_path> -m <model> --dangerously-skip-permissions`.
    Credentials are passed via OPENAI_API_KEY + OPENAI_BASE_URL env vars only.
    Platform review agent interception (D-078) deferred to R11.
    """

    agent_type = "opencode_cli"

    def is_available(self) -> bool:
        return is_opencode_available()

    async def invoke_coding_task(
        self,
        task: str,
        context_path: str,
        config: dict | None = None,
    ) -> dict:
        config = config or {}

        if not is_opencode_available():
            return _err("opencode CLI not found in PATH")

        ctx = Path(context_path)
        if not ctx.is_dir():
            return _err(f"context_path does not exist: {context_path}")

        env = _build_opencode_env(config)
        has_key = bool(env.get("OPENAI_API_KEY"))
        model = _pick_model(config, has_key)
        timeout = int(config.get("timeout_seconds", 180))

        cmd = [
            "opencode", "run", task,
            "--dir", str(ctx),
            "-m", model,
            "--dangerously-skip-permissions",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ctx),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return _err(f"Timed out after {timeout}s", issues=[f"timeout={timeout}s"])

            exit_code = proc.returncode
            raw = _strip_ansi(stdout_b.decode("utf-8", errors="replace"))[:_OUTPUT_CAP]
            stderr_text = _strip_ansi(stderr_b.decode("utf-8", errors="replace"))[:4096]

        except Exception as exc:
            return _err(f"Failed to launch opencode: {exc}")

        changed = await _git_changed_files(str(ctx))
        summary = _extract_summary(raw) or ("Task completed" if exit_code == 0 else "Task failed")
        issues = [stderr_text] if exit_code != 0 and stderr_text else []

        return {
            "status": "ok" if exit_code == 0 else "error",
            "reason": stderr_text if exit_code != 0 else "",
            "summary": summary,
            "changed_files": changed,
            "diff_ref": None,  # R11: write diff to artifact storage
            "issues": issues,
            "raw_output": raw,
            "exit_code": exit_code,
            "model": model,
            "agent_type": self.agent_type,
        }


def _err(reason: str, issues: list[str] | None = None) -> dict:
    return {
        "status": "error",
        "reason": reason,
        "summary": "",
        "changed_files": [],
        "diff_ref": None,
        "issues": issues or [reason],
        "raw_output": "",
        "exit_code": -1,
        "agent_type": "opencode_cli",
    }


# ── qcode CLI adapter ─────────────────────────────────────────────────────

class QCodeCLIAdapter:
    """Adapter for qcode (or other CLI-based AI coding agents). R11: real invocation."""

    agent_type = "qcode_cli"

    def is_available(self) -> bool:
        return shutil.which("qcode") is not None

    async def invoke_coding_task(
        self,
        task: str,
        context_path: str,
        config: dict | None = None,
    ) -> dict:
        return {
            "status": "not_implemented",
            "reason": "Real qcode invocation deferred to R11 (P4 execution chain, D-078)",
            "summary": "Stub — no task was executed",
            "changed_files": [],
            "diff_ref": None,
            "issues": [],
            "raw_output": "",
        }


# ── Platform agent adapter (default) ─────────────────────────────────────

class PlatformAgentAdapter:
    """Routes to LangGraph P4 execution nodes (R11). R8: stub."""

    agent_type = "platform_agent"

    def is_available(self) -> bool:
        return True

    async def invoke_coding_task(
        self,
        task: str,
        context_path: str,
        config: dict | None = None,
    ) -> dict:
        return {
            "status": "not_implemented",
            "reason": "Platform LangGraph agent P4 execution deferred to R11",
            "summary": "Stub — no task was executed",
            "changed_files": [],
            "diff_ref": None,
            "issues": [],
            "raw_output": "",
        }


# ── Factory ───────────────────────────────────────────────────────────────

_ADAPTER_MAP = {
    "opencode_cli": OpenCodeCLIAdapter,
    "qcode_cli": QCodeCLIAdapter,
    "platform_agent": PlatformAgentAdapter,
}


def get_coding_agent_adapter(agent_type: str) -> CodingAgentAdapter:
    cls = _ADAPTER_MAP.get(agent_type)
    if cls is None:
        raise ValueError(f"Unknown coding agent type: {agent_type!r}")
    return cls()
