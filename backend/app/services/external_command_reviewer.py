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

    __slots__ = ("verdict", "risk_level", "reason", "authorization")

    def __init__(self, verdict: str, risk_level: str, reason: str, authorization: dict) -> None:
        # verdict: "deny" | "auto_approved" | "require_confirmation" | "escalate"
        self.verdict = verdict
        self.risk_level = risk_level
        self.reason = reason
        self.authorization = authorization  # full mode_policy output dict

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
        }


def review(
    command: str,
    *,
    mode: str = "plan",
    in_plan: bool = False,
) -> ReviewDecision:
    """Evaluate a shell command request from an external platform.

    Args:
        command: The raw command string (e.g. "python3 test.py").
        mode: Project execution mode ("manual" / "plan" / "auto").
        in_plan: Whether this command is pre-planned in the current stage plan.

    Returns:
        ReviewDecision with verdict and risk level.
    """
    cmd_lower = command.strip().lower()

    # ── Step a: deny-list pre-filter ──────────────────────────────────────
    for pattern in DENY_SUBSTRINGS:
        if pattern.lower() in cmd_lower:
            auth = authorize_action(mode, _RISK_DENY, command, in_plan=False, confirmed=False)
            log.warning(
                "external_command_reviewer: DENY command=%r matched deny-list pattern=%r",
                command[:120], pattern,
            )
            return ReviewDecision(
                verdict="deny",
                risk_level=_RISK_DENY,
                reason=f"Blocked by deny-list (pattern: {pattern!r}). Command rejected unconditionally.",
                authorization=auth,
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

    # ── Step c: mode_policy authorization ─────────────────────────────────
    auth = authorize_action(mode, risk_level, command, in_plan=in_plan, confirmed=False)
    decision = auth["decision"]  # auto_approved / require_confirmation / escalate

    if decision == "auto_approved":
        verdict = "auto_approved"
        reason = auth["reason"]
    elif decision in ("require_confirmation", "escalate"):
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
    )
