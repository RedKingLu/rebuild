"""Execution provider seam — selects HOW agent/OpenCode-generated code runs.

This is the reserved abstraction for the containerization plan (Phase C2). All
code execution in the platform (Workspace / OpenCode integration) must go through
an ExecutionProvider so that the *where* (host subprocess vs isolated container)
is a configuration choice, not hard-wired into call sites.

Modes (env `EXECUTION_MODE`, default "local"):
  - "local"     → LocalSubprocessExecutionProvider: current behavior — sandboxed
                  host subprocess behind the deny-list (opencode_adapter). Backward
                  compatible; this is what ships today.
  - "container" → ContainerExecutionProvider: RESERVED. Runs code in a disposable,
                  network-isolated, non-root, resource-limited container
                  (deploy/execution-sandbox). Returns a clear "reserved" result
                  until wired — never silently downgrades to host execution.

Future Workspace/Run construction MUST target this interface, not call
opencode_adapter directly, so containerized execution can be enabled by config.
See 文档/02-架构设计/06-容器化部署与执行隔离规范.md.
"""

from __future__ import annotations

import os
from typing import Protocol

from app.services.opencode_adapter import execute as _local_execute


class ExecutionProvider(Protocol):
    """Contract every execution backend implements."""

    name: str

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None) -> dict:
        """Run code and return the standard ExecutionResult dict.

        Result keys: exit_code, stdout, stderr, elapsed_ms, provider, fallback,
        blocked. Implementations must enforce their own safety boundary and must
        never raise for ordinary execution failures (encode them in the result).
        """
        ...


class LocalSubprocessExecutionProvider:
    """Host-subprocess execution behind the deny-list (current default)."""

    name = "local_subprocess"

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None) -> dict:
        result = await _local_execute(code=code, language=language,
                                      timeout=timeout, model=model)
        result.setdefault("execution_mode", "local")
        return result


class ContainerExecutionProvider:
    """RESERVED (Phase C2): execute inside a disposable isolated container.

    Intentionally NOT yet implemented. It returns an explicit, honest "reserved"
    result rather than falling back to host execution — silent downgrade to a
    weaker isolation level would be a security regression. Wiring this is tracked
    as the containerization plan's execution-core.
    """

    name = "container_sandbox"

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None) -> dict:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": ("容器化执行（execution-sandbox）尚未接线，属预留能力（Phase C2）。"
                       "请将 EXECUTION_MODE 置回 local，或先完成沙箱接线。"),
            "elapsed_ms": 0,
            "provider": "container_sandbox",
            "execution_mode": "container",
            "fallback": False,
            "blocked": False,
            "reserved": True,
        }


def get_execution_provider() -> ExecutionProvider:
    """Return the execution provider selected by EXECUTION_MODE (default local)."""
    mode = (os.environ.get("EXECUTION_MODE") or "local").strip().lower()
    if mode == "container":
        return ContainerExecutionProvider()
    return LocalSubprocessExecutionProvider()
