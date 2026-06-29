"""Execution-mode authorization policy (R9-3F).

Implements the real behavioral difference between the three execution modes
(D-025 / D-081) and the Auto authorization proxy required by R9:

  - manual: every non-trivial action requires explicit user confirmation.
  - plan:   in-plan / low-risk (L0-L2) actions auto-pass; higher risk → confirm.
  - auto:   L0-L3 stage-internal actions auto-authorized by the Auto proxy;
            L4+ high-risk actions are NEVER auto-approved (require user/gate).

Stage-promotion Gates are out of scope here — they are always user-confirmed
regardless of mode (enforced by GateService), so Auto cannot bypass them.
"""

from __future__ import annotations

RISK_ORDER = ["L0", "L1", "L2", "L3", "L4", "L5"]
HIGH_RISK_FLOOR = RISK_ORDER.index("L4")


# ── Action → risk map (R9-5-7 Q-5, single source) ─────────────────────────
# One authoritative mapping from an action/tool type to its risk level, so the
# chat tool loop (agent_loop), risk_assess, and policy_check all classify the
# same action identically instead of each hardcoding a value.
#   read-only queries        → L1
#   compute / writes artifact → L2
#   profiling / stage work    → L3
#   command / write to disk   → L4 (high-risk floor: never auto in Auto)
ACTION_RISK: dict[str, str] = {
    "get_project_info": "L1",
    "read_artifact": "L1",
    "list_artifacts": "L1",
    "query": "L1",
    "run_profiling": "L3",
    "write_artifact": "L2",
    "write_file": "L3",
    "execute_command": "L4",
    "command": "L4",
    "remote_exec": "L4",
}
DEFAULT_ACTION_RISK = "L2"


def risk_for_action(action_type: str) -> str:
    """Return the risk level for an action/tool type (single source, Q-5)."""
    return ACTION_RISK.get((action_type or "").strip(), DEFAULT_ACTION_RISK)


def _rank(risk: str) -> int:
    try:
        return RISK_ORDER.index((risk or "L1").upper())
    except ValueError:
        return RISK_ORDER.index("L1")


def authorize_action(mode: str, risk_level: str, action: str = "",
                     *, in_plan: bool = False, confirmed: bool = False) -> dict:
    """Return a standard authorization decision for an action under a mode.

    Output fields (R9 MODE-03 contract): decision, reason, risk_level, mode,
    reviewer, evidence_refs, trace_refs, audit_ref.
    decision ∈ {auto_approved, approved, require_confirmation, escalate}.
    """
    mode = (mode or "plan").lower()
    risk = (risk_level or "L1").upper()
    is_high = _rank(risk) >= HIGH_RISK_FLOOR

    if is_high:
        # L4+ is never auto-approved by Auto; needs explicit confirmation/Gate.
        decision = "approved" if confirmed else "require_confirmation"
        reason = "高风险动作(L4+)：Auto 不代理，须用户显式确认或经 Gate"
    elif mode == "manual":
        decision = "approved" if confirmed else "require_confirmation"
        reason = "Manual 模式：每步动作需用户确认"
    elif mode == "plan":
        if in_plan or _rank(risk) <= RISK_ORDER.index("L2"):
            decision = "auto_approved"
            reason = "Plan 模式：计划内/低风险(≤L2)动作放行"
        else:
            decision = "approved" if confirmed else "require_confirmation"
            reason = "Plan 模式：计划外较高风险(L3)动作需用户确认"
    else:  # auto
        decision = "auto_approved"
        reason = "Auto 模式：阶段内 L0-L3 动作由 Auto 授权代理自动放行"

    return {
        "decision": decision,
        "reason": reason,
        "risk_level": risk,
        "mode": mode,
        "reviewer": f"{mode}_authorizer",
        "action": action,
        "evidence_refs": [],
        "trace_refs": [],
        "audit_ref": None,
    }
