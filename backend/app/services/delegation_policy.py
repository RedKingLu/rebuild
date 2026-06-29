"""Delegation policy — decides whether a stage should be delegated to an external platform.

D-088③ / R9-5-5 T2.2: `should_delegate(project, stage)` is the single decision
point for "does this stage run in the platform or in an external platform?"

Truth table (§4.3 of the R9-5-5 spec):

    coding_agent_ref = None                    → False  (always platform-native)
    external_platform_scope = "none"           → False
    external_platform_scope = "coding_only"
        stage == "P4"                          → True
        stage != "P4"                          → False
    external_platform_scope = "all_stages"     → True  (P0-P6)
"""

from __future__ import annotations

# Stages that constitute the full P0-P6 pipeline
P_STAGES = {"P0", "P1", "P2", "P3", "P4", "P5", "P6"}

# Scope constants
SCOPE_NONE = "none"
SCOPE_CODING_ONLY = "coding_only"
SCOPE_ALL_STAGES = "all_stages"

_VALID_SCOPES = {SCOPE_NONE, SCOPE_CODING_ONLY, SCOPE_ALL_STAGES}


def should_delegate(project: object, stage: str) -> bool:
    """Return True when this stage should be delegated to the external platform.

    ``project`` is any object (ORM model, dict, or Pydantic schema) that
    exposes ``coding_agent_ref`` and ``external_platform_scope`` attributes/keys.

    Raises:
        ValueError: if external_platform_scope is not a recognised value.
    """
    ref = _get(project, "coding_agent_ref")
    if not ref:
        return False

    scope = _get(project, "external_platform_scope") or SCOPE_NONE
    if scope not in _VALID_SCOPES:
        raise ValueError(
            f"Unknown external_platform_scope value {scope!r}. "
            f"Expected one of: {sorted(_VALID_SCOPES)}"
        )

    if scope == SCOPE_NONE:
        return False

    if scope == SCOPE_ALL_STAGES:
        return stage.upper() in P_STAGES

    # coding_only → only P4
    return stage.upper() == "P4"


def _get(obj: object, attr: str) -> object | None:
    """Get attribute from dict or object."""
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)
