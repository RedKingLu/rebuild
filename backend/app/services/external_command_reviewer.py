"""External command reviewer — platform audit Agent for OpenCode command requests.

D-088③ / D-087 / D-076 / R9-5-5 T6.1:
When OpenCode (or another external platform) raises a permission request for
a shell command, this reviewer evaluates it through three steps:

  a) deny-list pre-filter (DENY_SUBSTRINGS from ExecutionProvider) — instant reject
  b) allow-list / risk classification — known-safe commands get a low risk level
  c) mode_policy.authorize_action — decides auto_approved / require_confirmation / escalate

After a decision of "auto_approved" the caller should route the command to
`get_execution_provider().execute(...)` (D-076 contract: OpenCode does NOT run
commands directly; the platform runs them on OpenCode's behalf).

The reviewer does NOT execute the command itself — it only decides.

Design notes:
  - Default-deny: any command not in ALLOWED_COMMANDS is high-risk unless explicitly
    overridden.  This is intentional — better to trigger HITL too often than too little.
  - HITL integration: when authorize_action returns require_confirmation or escalate,
    the caller should invoke the LangGraph _gate() / interrupt() mechanism (D-087).
    This reviewer does not directly call interrupt() because it is not inside a graph
    node; callers (ACP client / Delegator) do that.
  - Audit trail: callers must write an AuditWriter record for every deny and every
    HITL escalation (T6.4 / G9).
"""

from __future__ import annotations

import logging

from app.services.execution_provider import ALLOWED_COMMANDS, DENY_SUBSTRINGS
from app.services.mode_policy import authorize_action

log = logging.getLogger("rebuild.external_command_reviewer")

# Risk levels assigned to commands
_RISK_DENY = "L5"            # deny-list match — never allowed
_RISK_ALLOWLISTED = "L1"     # known-safe allow-list command
_RISK_UNKNOWN = "L3"         # not in allow-list; elevated risk


class ReviewDecision:
    """Result of a command review."""

    __slots__ = ("verdict", "risk_level", "reason", "authorization", "risk_explanation", "audit_ref")

    def __init__(self, verdict: str, risk_level: str, reason: str, authorization: dict,
                 risk_explanation: dict | None = None, audit_ref: str | None = None) -> None:
        # verdict: "deny" | "auto_approved" | "require_confirmation" | "escalate"
        self.verdict = verdict
        self.risk_level = risk_level
        self.reason = reason
        self.authorization = authorization  # full mode_policy output dict
        self.risk_explanation = risk_explanation or {}  # WP-4 GAP-SEC-1: user-readable why
        self.audit_ref = audit_ref                       # WP-4 GAP-SEC-1: audit trail id

    def is_allowed(self) -> bool:
        return self.verdict == "auto_approved"

    def requires_hitl(self) -> bool:
        return self.verdict in ("require_confirmation", "escalate")

    def is_denied(self) -> bool:
        return self.verdict == "deny"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "authorization": self.authorization,
            "risk_explanation": self.risk_explanation,
            "audit_ref": self.audit_ref,
        }


def _authorize(mode: str, risk_level: str, command: str, *, in_plan: bool,
               project_id: str | None, run_id: str | None,
               agent_reviewer=None) -> dict:
    """Route the authorization through the SecurityAuthorizationService final-interception
    layer (WP-4 / GAP-SEC-1): Policy floor + optional Agent advice (clamped by Policy) +
    risk explanation + Audit. Falls back to the raw deterministic Policy when the services
    container is unavailable (e.g. unit tests without app context) so the reviewer never
    silently loses its floor."""
    try:
        from app.dependencies import get_services
        sec = get_services().security_authorization
        return sec.authorize(
            mode=mode, risk_level=risk_level, action=command[:120],
            in_plan=in_plan, project_id=project_id, run_id=run_id,
            agent_reviewer=agent_reviewer,
        )
    except Exception:
        # Honest fallback to the deterministic Policy floor (never loosen). 发声 in logs.
        log.warning("external_command_reviewer: SecurityAuthorization 不可用，回落 Policy 底线",
                    exc_info=True)
        out = authorize_action(mode, risk_level, command, in_plan=in_plan, confirmed=False)
        out["policy_decision"] = out["decision"]
        out["risk_explanation"] = {"why": out["reason"], "final_decision": out["decision"],
                                   "policy_is_floor": True}
        out["audit_ref"] = None
        return out


def review(
    command: str,
    *,
    mode: str = "plan",
    in_plan: bool = False,
    project_id: str | None = None,
    run_id: str | None = None,
    agent_reviewer=None,
) -> ReviewDecision:
    """Evaluate a shell command request from an external platform.

    Args:
        command: The raw command string (e.g. "python3 test.py").
        mode: Project execution mode ("manual" / "plan" / "auto").
        in_plan: Whether this command is pre-planned in the current stage plan.
        project_id / run_id: context for the Audit trail (GAP-SEC-1).
        agent_reviewer: optional Security/Authorization Agent advice callable (extension
            slot). Its advice can only tighten the decision, never loosen the Policy floor.

    Returns:
        ReviewDecision with verdict, risk level, risk explanation and audit ref.
    """
    cmd_lower = command.strip().lower()

    # ── Step a: deny-list pre-filter ──────────────────────────────────────
    for pattern in DENY_SUBSTRINGS:
        if pattern.lower() in cmd_lower:
            auth = _authorize(mode, _RISK_DENY, command, in_plan=False,
                              project_id=project_id, run_id=run_id)
            log.warning(
                "external_command_reviewer: DENY command=%r matched deny-list pattern=%r",
                command[:120], pattern,
            )
            return ReviewDecision(
                verdict="deny",
                risk_level=_RISK_DENY,
                reason=f"Blocked by deny-list (pattern: {pattern!r}). Command rejected unconditionally.",
                authorization=auth,
                risk_explanation=auth.get("risk_explanation"),
                audit_ref=auth.get("audit_ref"),
            )

    # ── Step b: allow-list / risk classification ──────────────────────────
    first_word = cmd_lower.split()[0].split("/")[-1] if cmd_lower else ""
    if first_word in {c.lower() for c in ALLOWED_COMMANDS}:
        risk_level = _RISK_ALLOWLISTED
    else:
        risk_level = _RISK_UNKNOWN
        log.debug(
            "external_command_reviewer: command=%r not in allow-list, risk=%s",
            command[:80], risk_level,
        )

    # ── Step c: SecurityAuthorization (Policy floor + Agent advice + Audit) ──
    auth = _authorize(mode, risk_level, command, in_plan=in_plan,
                      project_id=project_id, run_id=run_id, agent_reviewer=agent_reviewer)
    decision = auth["decision"]  # auto_approved / require_confirmation / escalate / deny

    if decision == "auto_approved":
        verdict = "auto_approved"
        reason = auth["reason"]
    elif decision in ("require_confirmation", "escalate", "deny"):
        verdict = decision
        reason = auth["reason"]
    else:
        # Fallback — treat any unknown decision as require_confirmation
        verdict = "require_confirmation"
        reason = f"Unknown authorization decision {decision!r} — defaulting to require_confirmation."

    log.debug(
        "external_command_reviewer: command=%r risk=%s verdict=%s",
        command[:80], risk_level, verdict,
    )
    return ReviewDecision(
        verdict=verdict,
        risk_level=risk_level,
        reason=reason,
        authorization=auth,
        risk_explanation=auth.get("risk_explanation"),
        audit_ref=auth.get("audit_ref"),
    )
