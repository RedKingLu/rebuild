"""AI Coding Agent adapter — interface for external AI coding agents.

D-076 (2026-06-26): OpenCode/qcode are AI coding agents that accept natural-language
tasks, NOT shell wrappers for running subprocess commands.

D-079 (2026-06-27): OpenCodeCLIAdapter now uses `opencode serve` + ACP protocol
instead of `opencode run --dangerously-skip-permissions`.  Permission requests
are intercepted via SSE and evaluated by PermissionPolicy.
Credentials are passed only as env vars to the subprocess (never logged/stored in plain text).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Protocol

from app.services.opencode_acp_client import OpenCodeACPClient
from app.services.opencode_permission_handler import PermissionPolicy
from app.services.opencode_server import OpenCodeServer

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

# D-098 / B-ORCH-02: no hardcoded model or endpoint — ALL model resolution
# goes through OpenCodeModelResolver → ModelGateway.  There is no fallback
# free model: if the platform has no configured provider, the call fails
# explicitly so the user knows to configure a credential (G2 / G8).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFJ]")
_OUTPUT_CAP = 32_768   # 32 KB cap on raw_output stored in response


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _build_opencode_env(config: dict) -> dict:
    """Build subprocess env: inject OPENAI_API_KEY + OPENAI_BASE_URL, strip LLM_API_KEY.

    Model/base/key resolve from the platform strategy (D-098), no hardcoded endpoint.
    Key is never returned or logged — it only lives in the subprocess env dict.
    """
    from app.services.opencode_server import resolve_model_defaults
    env = os.environ.copy()
    target = resolve_model_defaults(config)
    api_key = target.get("api_key") or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = target.get("base_url") or os.environ.get("OPENAI_BASE_URL") or os.environ.get("LLM_BASE_URL")
    if api_key and base_url:
        env["OPENAI_API_KEY"] = api_key
        env["OPENAI_BASE_URL"] = base_url
    env.pop("LLM_API_KEY", None)
    return env


def _pick_model(
    config: dict,
    *,
    project_id: str | None = None,
    strategy_id: str = "system-default",
    user_override: str | None = None,
) -> str:
    """Return the model string to pass to opencode — from the platform strategy (D-098).

    Raises RuntimeError if no model can be resolved (G2: no hardcoded fallback).
    """
    from app.services.opencode_server import resolve_model_defaults
    target = resolve_model_defaults(
        config, project_id=project_id, strategy_id=strategy_id, user_override=user_override
    )
    if target.get("model"):
        return target["model"]
    raise RuntimeError(
        "No model could be resolved for OpenCode — "
        "configure a provider and credential in the platform settings (D-088⑤)."
    )


def _extract_summary_from_messages(messages: list[dict]) -> str:
    """Extract a short summary from the last assistant message."""
    for msg in reversed(messages):
        role = msg.get("role", "")
        if role != "assistant":
            continue
        for part in msg.get("parts", []):
            if part.get("type") == "text":
                text = _strip_ansi(part.get("text", ""))
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                if lines:
                    return lines[-1][:256]
    return ""


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

    Uses `opencode serve` + ACP protocol (D-079).  Permission requests raised
    by opencode are intercepted via SSE and evaluated by PermissionPolicy —
    no `--dangerously-skip-permissions` needed.
    Credentials are injected via OPENAI_API_KEY / OPENAI_BASE_URL env vars only.
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

        try:
            model = _pick_model(
                config,
                project_id=config.get("project_id"),
                strategy_id=config.get("strategy_id", "system-default"),
                user_override=config.get("model_override"),
            )
        except RuntimeError as exc:
            return _err(str(exc))
        timeout = int(config.get("timeout_seconds", 180))
        policy = PermissionPolicy()

        try:
            async with OpenCodeServer(str(ctx), config) as server:
                client = OpenCodeACPClient(server.base_url, server.password)
                session_id = await client.create_session(model)
                await client.send_message(session_id, task)
                idle = await client.run_until_idle(
                    session_id, policy, str(ctx), timeout
                )
                messages = await client.get_messages(session_id)
        except RuntimeError as exc:
            return _err(str(exc))
        except Exception as exc:
            return _err(f"ACP invocation failed: {exc}")

        changed = await _git_changed_files(str(ctx))
        summary = _extract_summary_from_messages(messages) or (
            "Task completed" if idle["status"] == "ok" else "Task failed"
        )
        issues: list[str] = []
        if idle["status"] != "ok":
            issues.append(f"session ended with: {idle.get('idle_reason', 'unknown')}")

        return {
            "status": idle["status"],
            "reason": idle.get("idle_reason", ""),
            "summary": summary,
            "changed_files": changed,
            "diff_ref": None,
            "issues": issues,
            "raw_output": "",
            "exit_code": 0 if idle["status"] == "ok" else 1,
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
