"""OpenCode server lifecycle manager (D-079).

Starts `opencode serve --port 0` as a subprocess, parses the announced URL
from stdout, and tears it down cleanly on context exit.

Usage::

    async with OpenCodeServer(workspace_path, config) as server:
        # server.base_url  → "http://127.0.0.1:<PORT>"
        # server.password  → random secret for this session only

Security: OPENCODE_SERVER_PASSWORD is generated fresh each invocation with
secrets.token_hex(16) and is never logged, stored, or returned in responses.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
from pathlib import Path

_READY_RE = re.compile(r"opencode server listening on (http://127\.0\.0\.1:\d+)")
_STARTUP_TIMEOUT = 10  # seconds to wait for the URL line


def resolve_model_defaults(
    config: dict,
    project_id: str | None = None,
    strategy_id: str = "system-default",
    user_override: str | None = None,
) -> dict:
    """Resolve (model, base_url, api_key) for opencode — D-098 single source of truth.

    Precedence: explicit per-agent config (custom mode) → platform strategy
    (OpenCodeModelResolver → ModelGateway.resolve_call_target).  No hardcoded
    model/endpoint (B-ORCH-02).  If resolution fails, returns dict with all
    None values — caller is responsible for raising a clear error (G8).
    """
    model = config.get("model")
    base_url = config.get("base_url")
    api_key = None
    if not (model and base_url):
        try:
            from app.services.opencode_model_resolver import OpenCodeModelResolver
            resolved = OpenCodeModelResolver().resolve(
                project_id=project_id,
                strategy_id=strategy_id,
                user_override=user_override,
            )
            model = model or resolved["model_id"]
            base_url = base_url or resolved["base_url"]
            api_key = resolved["api_key"]
        except Exception:
            pass
    return {"model": model, "base_url": base_url, "api_key": api_key}


def _build_serve_env(config: dict, password: str) -> dict:
    """Build subprocess env: API key + base URL + server password.

    Model/base/key resolve from the platform strategy (D-098) — no hardcoded endpoint.
    LLM_API_KEY is mapped to OPENAI_API_KEY and then removed from the env so
    it never appears in log output.  The generated password lives only in the
    returned dict for the duration of the subprocess.
    """
    env = os.environ.copy()
    target = resolve_model_defaults(config)
    api_key = (
        target.get("api_key")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    base_url = (
        target.get("base_url")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
    )
    if api_key and base_url:
        env["OPENAI_API_KEY"] = api_key
        env["OPENAI_BASE_URL"] = base_url
    env.pop("LLM_API_KEY", None)
    env["OPENCODE_SERVER_PASSWORD"] = password
    return env


class OpenCodeServer:
    """Async context manager that owns one `opencode serve` subprocess.

    Attributes:
        base_url: The http://127.0.0.1:<port> URL after a successful start.
        password: The OPENCODE_SERVER_PASSWORD for this server instance.
    """

    def __init__(self, workspace_path: str, config: dict | None = None) -> None:
        self._workspace = str(Path(workspace_path).resolve())
        self._config = config or {}
        self.password: str = secrets.token_hex(16)
        self.base_url: str = ""
        self._proc: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> "OpenCodeServer":
        await self._start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._stop()

    # ── Internal ──────────────────────────────────────────────────────────

    async def _start(self) -> None:
        if not shutil.which("opencode"):
            raise RuntimeError("opencode CLI not found in PATH")

        env = _build_serve_env(self._config, self.password)
        self._proc = await asyncio.create_subprocess_exec(
            "opencode", "serve", "--port", "0",
            cwd=self._workspace,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            self.base_url = await asyncio.wait_for(
                self._wait_for_ready(), timeout=_STARTUP_TIMEOUT
            )
        except asyncio.TimeoutError:
            await self._stop()
            raise RuntimeError(
                f"opencode serve did not become ready within {_STARTUP_TIMEOUT}s"
            )

    async def _wait_for_ready(self) -> str:
        assert self._proc and self._proc.stdout
        async for raw_line in self._proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            m = _READY_RE.search(line)
            if m:
                return m.group(1)
        raise RuntimeError("opencode serve stdout closed without announcing URL")

    async def _stop(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
