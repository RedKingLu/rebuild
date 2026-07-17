"""OpenCode ACP HTTP + SSE client (D-079 / D-088③④).

Wraps the opencode ACP (Agent Client Protocol) REST + SSE API exposed by
`opencode serve`.  Handles session lifecycle, message sending, SSE event
streaming, and per-request permission replies.

R9-5-5 T6.3: Permission dispatch now routes through platform intermediaries:
  - write permissions → WorkspaceMediator (D-088②)
  - exec permissions  → external_command_reviewer (D-088③) + mode_policy + HITL
  - read permissions  → fast-path allow (boundary guard in mediator)
  Fallback: if workspace_path is not provided, reverts to PermissionPolicy (legacy).

R9-5-5 T6.4: Every permission decision is written to TraceWriter; denied /
high-risk decisions are also written to AuditWriter (G9).

All requests use HTTP Basic auth (username="opencode", password=server secret).
The password is never logged or returned in any response.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Optional

import httpx

from app.services.opencode_permission_handler import PermissionPolicy

log = logging.getLogger(__name__)

_SSE_PREFIX = "data: "
# SSE event types that signal task completion
_IDLE_TYPES = {"session.idle", "session.error", "error"}
# SSE event types that carry permission requests
_PERM_V1_TYPE = "permission.asked"
_PERM_V2_TYPE = "permission.v2.asked"


def _auth_header(password: str) -> dict[str, str]:
    raw = f"opencode:{password}"
    encoded = base64.b64encode(raw.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


class OpenCodeACPClient:
    """HTTP client for one `opencode serve` instance.

    Args:
        base_url: e.g. "http://127.0.0.1:12353"
        password: OPENCODE_SERVER_PASSWORD for Basic auth
    """

    def __init__(self, base_url: str, password: str, *,
                 hitl_resolver=None, hitl_gate_timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {**_auth_header(password), "Content-Type": "application/json"}
        # GAP-HITL-1 (WP-4): interactive HITL for high-risk external-agent requests.
        # `hitl_resolver(project_id, run_id, command, decision_obj) -> "once"|"reject"`
        # drives the ACP reply from a real user Gate decision (replaces the blind reject
        # bottom). Injectable for testing; defaults to the Gate-based resolver below.
        self._hitl_resolver = hitl_resolver
        self._hitl_gate_timeout = hitl_gate_timeout

    # ── Session ───────────────────────────────────────────────────────────

    async def create_session(self, model_id: str) -> str:
        """Create a new session and return its id."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/session",
                headers=self._headers,
                json={"modelID": model_id},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["id"]

    # ── Messaging ─────────────────────────────────────────────────────────

    async def send_message(self, session_id: str, text: str) -> None:
        """Send a user message to the session."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/session/{session_id}/message",
                headers=self._headers,
                json={"parts": [{"type": "text", "text": text}]},
                timeout=15,
            )
            resp.raise_for_status()

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Retrieve all messages in a session."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/session/{session_id}/messages",
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # The API may return {"messages": [...]} or a bare list
            if isinstance(data, list):
                return data
            return data.get("messages", [])

    # ── Permission reply helpers ──────────────────────────────────────────

    async def _reply_v1(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        permission_id: str,
        decision: str,
    ) -> None:
        url = f"{self._base}/session/{session_id}/permissions/{permission_id}"
        await client.post(url, headers=self._headers, json={"response": decision}, timeout=10)

    async def _reply_v2(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        request_id: str,
        decision: str,
    ) -> None:
        url = f"{self._base}/api/session/{session_id}/permission/{request_id}/reply"
        await client.post(url, headers=self._headers, json={"reply": decision}, timeout=10)

    # ── SSE event loop ────────────────────────────────────────────────────

    async def run_until_idle(
        self,
        session_id: str,
        policy: PermissionPolicy,
        workspace_path: str,
        timeout: int = 180,
        *,
        mode: str = "plan",
        project_id: Optional[str] = None,
        in_plan: bool = False,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Stream SSE events until the session becomes idle or errors out.

        Intercepts permission requests and replies according to the platform
        policy.  Returns a summary dict when the session completes.

        R9-5-5 T6.3: when workspace_path is set, routes write/exec permissions
        through WorkspaceMediator / external_command_reviewer.
        """
        try:
            result = await asyncio.wait_for(
                self._stream_loop(
                    session_id, policy, workspace_path,
                    mode=mode, project_id=project_id, in_plan=in_plan, run_id=run_id,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "idle_reason": f"timed_out_after_{timeout}s",
                "events_count": 0,
            }
        return result

    async def _stream_loop(
        self,
        session_id: str,
        policy: PermissionPolicy,
        workspace_path: str,
        *,
        mode: str = "plan",
        project_id: Optional[str] = None,
        in_plan: bool = False,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        events_count = 0
        idle_reason = "unknown"
        status = "ok"

        auth = {"Authorization": self._headers["Authorization"]}

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                f"{self._base}/event",
                headers=auth,
                timeout=None,
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    if not raw_line.startswith(_SSE_PREFIX):
                        continue
                    try:
                        event = json.loads(raw_line[len(_SSE_PREFIX):])
                    except json.JSONDecodeError:
                        continue

                    payload = event.get("payload", {})
                    etype = payload.get("type", "")
                    props = payload.get("properties", {})

                    # Only process events belonging to our session
                    sid = props.get("sessionID") or props.get("session_id", "")
                    if sid and sid != session_id:
                        continue

                    events_count += 1

                    if etype in _IDLE_TYPES:
                        idle_reason = etype
                        status = "ok" if etype == "session.idle" else "error"
                        break

                    elif etype == _PERM_V1_TYPE:
                        perm_id = props.get("permissionID") or props.get("id", "")
                        perm_kind = props.get("permission", "")
                        target = props.get("path") or props.get("command") or ""
                        decision = self._evaluate_permission(
                            perm_kind, target, workspace_path, policy,
                            mode=mode, project_id=project_id, in_plan=in_plan, run_id=run_id,
                        )
                        log.debug(
                            "perm_v1 %s target=%r decision=%s", perm_kind, target, decision
                        )
                        try:
                            await self._reply_v1(client, session_id, perm_id, decision)
                        except Exception as exc:
                            log.warning("Failed to reply to v1 permission: %s", exc)

                    elif etype == _PERM_V2_TYPE:
                        request_id = props.get("requestID") or props.get("id", "")
                        perm_kind = props.get("permission", "")
                        target = props.get("path") or props.get("command") or ""
                        decision = self._evaluate_permission(
                            perm_kind, target, workspace_path, policy,
                            mode=mode, project_id=project_id, in_plan=in_plan, run_id=run_id,
                        )
                        log.debug(
                            "perm_v2 %s target=%r decision=%s", perm_kind, target, decision
                        )
                        try:
                            await self._reply_v2(client, session_id, request_id, decision)
                        except Exception as exc:
                            log.warning("Failed to reply to v2 permission: %s", exc)

        return {
            "status": status,
            "idle_reason": idle_reason,
            "events_count": events_count,
        }

    # ── Permission evaluation (R9-5-5 T6.3) ─────────────────────────────

    def _evaluate_permission(
        self,
        perm_kind: str,
        target: str,
        workspace_path: str,
        policy: PermissionPolicy,
        *,
        mode: str,
        project_id: Optional[str],
        in_plan: bool,
        run_id: Optional[str] = None,
    ) -> str:
        """Route permission request through mediator/reviewer or fallback to policy.

        Returns "once" (allow) or "reject".
        """
        pt = perm_kind.lower().strip()

        # ── Read: fast allow (mediator boundary guard is passive) ─────────
        if pt in {"file_read", "read"}:
            _trace_permission(project_id, pt, target, "once", "read fast-allow")
            return "once"

        # ── Write: WorkspaceMediator boundary enforcement ─────────────────
        if pt in {"file_write", "write"}:
            if workspace_path:
                try:
                    from app.services.workspace_mediator import WorkspaceMediator
                    mediator = WorkspaceMediator(workspace_path)
                    _, risk = mediator.check_write(target)
                    _trace_permission(project_id, pt, target, "once", f"mediator allow risk={risk}")
                    return "once"
                except ValueError as exc:
                    _audit_permission(project_id, pt, target, "reject", str(exc))
                    _trace_permission(project_id, pt, target, "reject", str(exc)[:200])
                    return "reject"
                except Exception as exc:
                    log.warning("WorkspaceMediator error for %r: %s", target, exc)
                    return "reject"
            # Fallback: legacy policy
            return policy.decide(perm_kind, target, workspace_path)

        # ── Execute: external_command_reviewer ────────────────────────────
        if pt in {"bash", "execute", "shell", "command"}:
            if workspace_path:
                try:
                    from app.services.external_command_reviewer import review
                    decision_obj = review(target, mode=mode, in_plan=in_plan,
                                          project_id=project_id, run_id=run_id)
                    acp_decision = "once" if decision_obj.is_allowed() else "reject"

                    if decision_obj.is_denied():
                        _audit_permission(
                            project_id, pt, target, "deny",
                            decision_obj.reason, risk_level=decision_obj.risk_level,
                        )
                    elif decision_obj.requires_hitl():
                        # GAP-HITL-1 (WP-4): interactive HITL. A high-risk command no longer
                        # gets a blind reject — the platform opens a real user Gate and drives
                        # the ACP reply from the user's decision (approve → "once", reject /
                        # timeout → "reject"). D-087/D-088: platform審核 Agent + HITL 拦截.
                        acp_decision = self._resolve_hitl(
                            project_id, run_id, target, decision_obj)
                    else:
                        _trace_permission(
                            project_id, pt, target, "once",
                            f"reviewer allow risk={decision_obj.risk_level}",
                        )
                    return acp_decision
                except Exception as exc:
                    log.warning("external_command_reviewer error for %r: %s", target, exc)
                    return "reject"
            # Fallback: legacy policy
            return policy.decide(perm_kind, target, workspace_path)

        # Unknown — safe default
        return "reject"

    # ── Interactive HITL (GAP-HITL-1) ──────────────────────────────────────

    def _resolve_hitl(self, project_id, run_id, command: str, decision_obj) -> str:
        """Drive the ACP reply from a real user Gate decision (interactive HITL).

        Uses an injected resolver when provided (tests / custom wiring); otherwise the
        default Gate-based resolver creates an action_approval Gate and waits (bounded) for
        the user's decision. Never silently allows: on timeout / no-gate-backend it returns
        "reject" AND records the reason (honest degradation, not a blind reject).
        """
        if self._hitl_resolver is not None:
            try:
                return self._hitl_resolver(project_id, run_id, command, decision_obj)
            except Exception as exc:
                log.warning("hitl_resolver error for %r: %s — reject", command[:80], exc)
                _audit_permission(project_id, "command", command, "hitl_error",
                                  f"HITL resolver 异常，保守拒绝: {exc}",
                                  risk_level=decision_obj.risk_level)
                return "reject"
        return self._default_gate_hitl(project_id, run_id, command, decision_obj)

    def _default_gate_hitl(self, project_id, run_id, command: str, decision_obj) -> str:
        """Default interactive HITL: create a user Gate and poll (bounded) for its decision."""
        import time
        try:
            from app.dependencies import get_services
            gs = get_services().gate_service
        except Exception as exc:
            _audit_permission(project_id, "command", command, "hitl_required",
                              f"HITL 需用户确认，但无 Gate 服务可用（{exc}）→ 保守拒绝",
                              risk_level=decision_obj.risk_level)
            return "reject"
        try:
            gate = gs.create(
                project_id=project_id or "", run_id=run_id or "", stage="p4",
                gate_type="action_approval", risk_level=decision_obj.risk_level,
                reason=f"外部 Agent 高风险命令请求需用户确认：{command[:120]}",
                summary=(f"外部编程 Agent 请求执行高风险命令（风险 {decision_obj.risk_level}）："
                         f"{command[:120]}。请批准或拒绝。"),
                options=["approve", "reject"],
            )
        except Exception as exc:
            _audit_permission(project_id, "command", command, "hitl_required",
                              f"创建 HITL Gate 失败（{exc}）→ 保守拒绝",
                              risk_level=decision_obj.risk_level)
            return "reject"

        _trace_permission(project_id, "command", command, "hitl_gate_created",
                          f"交互式 HITL Gate {gate.gate_id} 已创建，等待用户决策")
        deadline = time.monotonic() + self._hitl_gate_timeout
        while time.monotonic() < deadline:
            try:
                cur = gs.get(gate.gate_id)
            except Exception:
                cur = None
            status = getattr(cur, "gate_status", "") if cur else ""
            if status == "approved":
                _audit_permission(project_id, "command", command, "hitl_approved",
                                  f"用户经 Gate {gate.gate_id} 批准高风险命令",
                                  risk_level=decision_obj.risk_level)
                return "once"
            if status in ("rejected", "changes_requested", "consumed"):
                _audit_permission(project_id, "command", command, "hitl_rejected",
                                  f"用户经 Gate {gate.gate_id} 拒绝高风险命令（{status}）",
                                  risk_level=decision_obj.risk_level)
                return "reject"
            time.sleep(0.5)
        _audit_permission(project_id, "command", command, "hitl_timeout",
                          f"HITL Gate {gate.gate_id} 在 {self._hitl_gate_timeout}s 内未获用户决策 → 保守拒绝",
                          risk_level=decision_obj.risk_level)
        return "reject"


# ── Trace / Audit helpers (T6.4) ─────────────────────────────────────────

def _trace_permission(
    project_id: Optional[str],
    perm_kind: str,
    target: str,
    decision: str,
    reason: str,
) -> None:
    try:
        from app.dependencies import get_services
        tw = get_services().trace_writer
        tw.write(
            "external_delegation",
            project_id=project_id,
            action=f"permission_{perm_kind}",
            summary=f"perm={perm_kind} target={target[:80]!r} decision={decision}: {reason[:120]}",
        )
    except Exception:
        log.warning("_trace_permission failed (non-fatal): perm=%s target=%r", perm_kind, target[:60], exc_info=True)


def _audit_permission(
    project_id: Optional[str],
    perm_kind: str,
    target: str,
    decision: str,
    reason: str,
    risk_level: str = "L3",
) -> None:
    try:
        from app.dependencies import get_services
        aw = get_services().audit_writer
        aw.write(
            "external_command_review",
            risk_level=risk_level,
            action=f"permission_{perm_kind}",
            decision=decision,
            reason=reason[:300],
            project_id=project_id,
        )
    except Exception:
        log.warning("_audit_permission failed (non-fatal): perm=%s", perm_kind, exc_info=True)
