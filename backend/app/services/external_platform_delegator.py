"""External platform delegator — orchestration pipeline for OpenCode delegation.

D-088③④ / R9-5-5 T3: `ExternalPlatformDelegator.delegate()` is the single
entry point for running a task in an external platform (currently OpenCode).

Pipeline (§4.4 of the R9-5-5 spec):
  1. build_external_context → context text + material files
  2. OpenCodeModelResolver.resolve → {model_id, base_url, api_key}
  3. OpenCodeServer start + ACPClient.create_session(model_id)
  4. ACPClient.send_message(session, context_text + task)
  5. ACPClient.run_until_idle → mediated permission loop (reviewer/mediator)
  6. git diff → changed_files; Trace summary; structured result return

Security:
  - api_key is used ONLY to build the subprocess env (OPENAI_API_KEY) and is
    never stored, returned, logged, or included in any response (G9).
  - Trace/Audit records are written for each delegation (T6.4).
  - OpenCode writes only to output_code/ (WorkspaceMediator enforces this).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from app.services.opencode_model_resolver import OpenCodeModelResolver, ResolverError
from app.services.opencode_adapter import is_opencode_available

log = logging.getLogger("rebuild.external_platform_delegator")


class DelegationError(Exception):
    """Raised when a delegation cannot proceed (stops the run, surfaces honest error)."""


class ExternalPlatformDelegator:
    """Orchestrates a full OpenCode delegation round-trip.

    Instantiated once per project; `delegate()` is called per task/stage.
    """

    def __init__(
        self,
        project_id: str,
        workspace_path: str,
        *,
        mode: str = "plan",
        in_plan: bool = False,
    ) -> None:
        self._project_id = project_id
        self._workspace = str(Path(workspace_path).resolve())
        self._mode = mode
        self._in_plan = in_plan
        self._resolver = OpenCodeModelResolver()

    async def delegate(
        self,
        task: str,
        stage: str = "P4",
        *,
        context_policy: str = "full",
        timeout: int = 300,
        project: object | None = None,
        run: object | None = None,
        agent_config: dict | None = None,
    ) -> dict:
        """Run a task via OpenCode under platform mediation.

        Returns:
            {
              status: "ok" | "error" | "timeout",
              summary: str,
              changed_files: list[str],
              issues: list[str],
              trace_refs: list[str],
            }

        Raises:
            DelegationError: If a hard precondition fails (no model, no CLI, etc.).
        """
        cfg = agent_config or {}
        project_id = self._project_id

        # ── Step 1: assemble context ──────────────────────────────────────
        context_text = ""
        material_files: list[str] = []
        try:
            from app.services.external_context_builder import build_external_context
            ctx_result = build_external_context(
                project_id, stage,
                context_policy=context_policy,
                project=project,
                run=run,
                task=task,
            )
            context_text = ctx_result["context_text"]
            material_files = ctx_result["material_files"]
        except Exception as exc:
            log.warning("Context assembly failed (non-fatal): %s", exc)
            context_text = f"# Task\n{task}\n"

        # ── Step 2: resolve model ─────────────────────────────────────────
        try:
            resolved = self._resolver.resolve(
                project_id=project_id,
                strategy_id=cfg.get("strategy_id", "system-default"),
                user_override=cfg.get("model_override"),
            )
        except ResolverError as exc:
            raise DelegationError(str(exc)) from exc

        model_id = resolved["model_id"]
        # api_key is used only in subprocess env — never logged or returned

        # ── Step 3+4+5: ACP session ───────────────────────────────────────
        if not is_opencode_available():
            raise DelegationError(
                "opencode CLI not found in PATH — cannot delegate task. "
                "Install OpenCode or switch to platform-native execution."
            )

        workspace = self._workspace
        if not Path(workspace).is_dir():
            raise DelegationError(f"Workspace directory does not exist: {workspace}")

        from app.services.opencode_server import OpenCodeServer
        from app.services.opencode_acp_client import OpenCodeACPClient
        from app.services.opencode_permission_handler import PermissionPolicy

        # Build env: inject key+base_url into subprocess, strip host LLM_API_KEY
        serve_config = {
            "model": model_id,
            "base_url": resolved["base_url"],
            # api_key injected internally by OpenCodeServer._build_serve_env
        }
        # Temporarily set env so resolve_model_defaults can pick it up
        _api_key = resolved["api_key"]
        _orig_openai_key = os.environ.get("OPENAI_API_KEY", "")
        _orig_openai_base = os.environ.get("OPENAI_BASE_URL", "")
        os.environ["OPENAI_API_KEY"] = _api_key
        os.environ["OPENAI_BASE_URL"] = resolved["base_url"]

        try:
            async with OpenCodeServer(workspace, serve_config) as server:
                client = OpenCodeACPClient(server.base_url, server.password)
                session_id = await client.create_session(model_id)

                # Inject context as first message
                full_message = context_text if context_text else task
                await client.send_message(session_id, full_message)

                idle = await client.run_until_idle(
                    session_id,
                    PermissionPolicy(),
                    workspace,
                    timeout,
                    mode=self._mode,
                    project_id=project_id,
                    in_plan=self._in_plan,
                )
                messages = await client.get_messages(session_id)
        except DelegationError:
            raise
        except RuntimeError as exc:
            return _delegation_err(str(exc))
        except Exception as exc:
            return _delegation_err(f"ACP invocation failed: {exc}")
        finally:
            # Clean up: restore original env vars (never leave key in env longer than needed)
            if _orig_openai_key:
                os.environ["OPENAI_API_KEY"] = _orig_openai_key
            else:
                os.environ.pop("OPENAI_API_KEY", None)
            if _orig_openai_base:
                os.environ["OPENAI_BASE_URL"] = _orig_openai_base
            else:
                os.environ.pop("OPENAI_BASE_URL", None)

        # ── Step 6: collect changed files + Trace ─────────────────────────
        from app.services.opencode_adapter import _git_changed_files, _extract_summary_from_messages
        changed = await _git_changed_files(workspace)
        summary = _extract_summary_from_messages(messages) or (
            "Task completed" if idle["status"] == "ok" else "Task failed"
        )
        issues: list[str] = []
        if idle["status"] != "ok":
            issues.append(f"session ended with: {idle.get('idle_reason', 'unknown')}")

        trace_refs = _write_delegation_trace(
            project_id, stage, task, idle["status"], summary, changed
        )

        return {
            "status": idle["status"],
            "reason": idle.get("idle_reason", ""),
            "summary": summary,
            "changed_files": changed,
            "issues": issues,
            "trace_refs": trace_refs,
            "model": model_id,
            "stage": stage,
            "agent_type": "opencode_cli",
        }


def _delegation_err(reason: str) -> dict:
    return {
        "status": "error",
        "reason": reason,
        "summary": "",
        "changed_files": [],
        "issues": [reason],
        "trace_refs": [],
        "agent_type": "opencode_cli",
    }


def _write_delegation_trace(
    project_id: str,
    stage: str,
    task: str,
    status: str,
    summary: str,
    changed_files: list[str],
) -> list[str]:
    """Write end-of-delegation summary to TraceWriter (T6.4). Returns trace_ref list."""
    try:
        from app.dependencies import get_services
        tw = get_services().trace_writer
        trace = tw.write(
            "external_delegation",
            project_id=project_id,
            stage=stage,
            action="delegation_complete",
            summary=f"stage={stage} status={status}: {summary[:200]}",
            extra={
                "task": task[:200],
                "changed_files_count": len(changed_files),
                "changed_files": changed_files[:20],
            },
        )
        return [trace.get("trace_id", "")]
    except Exception:
        return []
