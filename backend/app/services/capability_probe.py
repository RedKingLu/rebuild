"""Capability probe — real state truthfulness for the workspace aggregate (R9-5-8, T1).

Replaces the hardcoded `source_status="real"` / `capability_status="available"` /
`graph_capability_status="future_r10"` literals with values computed from real
backend facts (公理1 真实性单调 / 公理4 能力诚实). When a capability's backing
source is not ready, the probe returns the honest `not_connected` / `future` /
`deferred` — that honesty is the goal, not a defect (00-总规划 R9-5-8 §1.2).
"""

from __future__ import annotations

from app.services import workspace_service


# ── source_status: is the project's source code really present? ────────────

def probe_source_status(project_id: str, source_type: str,
                        workspace_status: str | None = None) -> str:
    """real / empty / deferred / blocked — from real source/ file count + status.

    - blocked  : materialization recorded an error (workspace_status == 'blocked')
    - real     : source/ has ≥1 file
    - empty    : manual project with no source (legitimately empty)
    - deferred : non-manual project with 0 files (needs creds / not yet imported)
    """
    if (workspace_status or "").lower() == "blocked":
        return "blocked"
    file_count = _source_file_count(project_id)
    if file_count > 0:
        return "real"
    if (source_type or "").lower() == "manual":
        return "empty"
    return "deferred"


def _source_file_count(project_id: str) -> int:
    src = workspace_service.workspace_path(project_id) / "source"
    if not src.exists():
        return 0
    skip = {".git", "node_modules", "__pycache__"}
    return sum(1 for p in src.rglob("*")
               if p.is_file() and not any(s in skip for s in p.parts))


# ── capability_status: are the platform's runtime capabilities reachable? ───

def probe_capability_status() -> str:
    """available / degraded / not_connected — combine graph + context wiring.

    Probes real facts (no optimism, S-2):
      - graph compiled/live (graph_capability_probe)
      - context_assembler import-wired (R9-5-3)
    available  : both live; degraded : one of them; not_connected : neither.
    """
    graph_ok = _graph_live()
    context_ok = _context_wired()
    if graph_ok and context_ok:
        return "available"
    if graph_ok or context_ok:
        return "degraded"
    return "not_connected"


def _graph_live() -> bool:
    try:
        from app.graph.runtime import graph_capability_probe
        return graph_capability_probe() == "live"
    except Exception:
        return False


def _context_wired() -> bool:
    """True if the unified context assembler is importable + exposes its entry.

    Probes the public `build_system_prompt` entry rather than the canonical
    assemble function, to avoid being mistaken for a competing caller of the
    single-source assembler (X-4-5 guard)."""
    try:
        from app.services import context_assembler
        return hasattr(context_assembler, "build_system_prompt")
    except Exception:
        return False


# ── graph_status: real LangGraph compile/runtime state ─────────────────────

def probe_graph_status() -> str:
    """connected / future / not_connected — from the real compile probe.

    'live' (graph compiles) → connected; otherwise the honest degraded state.
    """
    try:
        from app.graph.runtime import graph_capability_probe
        return "connected" if graph_capability_probe() == "live" else "not_connected"
    except Exception:
        return "future"
