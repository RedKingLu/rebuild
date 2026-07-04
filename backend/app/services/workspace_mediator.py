"""Workspace mediator — unified controlled file read/write boundary for ALL write actors.

D-088② / R9-5-5 T4 (external platforms) → **升格 D-099 / R11-3-C1**: this mediator is
the single write gatekeeper for BOTH platform-internal workers (P4 execution worker,
Tools) AND external platforms (e.g. OpenCode).  Every write actor MUST route writes
through this mediator — no actor may write the workspace via raw fs directly.  It enforces:

  - Read: workspace boundary guard (no path traversal, no escape).
  - Write: output directory enforcement (only output_code/, artifacts/, patches/
         are writable). source/ is READONLY — writes are rejected unconditionally
         for every actor (platform-internal or external), D-099①.
         patches/ is platform-writable for diff/patch drafts (D-099③ / D-104); it
         stays read-only for USERS at the workspace_service API layer (READONLY_DIRS)
         — "platform-writable, user-read-only".
  - Risk classification: L0-L3+ based on target directory and file type,
    fed back to the command reviewer / HITL for escalation decisions.

Write risk levels (used by external_command_reviewer / P4 execution worker):
  L0 — read (no write)
  L1 — write to output_code/ ordinary file (new or overwrite)
  L2 — write to artifacts/ or patches/, or overwrite an existing output_code/
       file with the same name (content update)
  L3 — write a script/executable file (.py, .sh, .js, etc.) to output_code/
  L4 — write to any path outside the allowed write dirs but inside workspace
  L5 — path traversal attempt / escape outside workspace root
"""

from __future__ import annotations

import os
from pathlib import Path


# ── Directory-level write policy ─────────────────────────────────────────

# The writable dirs for any actor (platform-internal worker or external), D-099 / D-104.
# patches/ holds diff/patch drafts (D-099③); platform-writable here, user-read-only at
# the workspace_service API layer.
_WRITABLE_DIRS = {"output_code", "artifacts", "patches"}

# source/ is unconditionally read-only for every actor (D-099①, 升格 D-088②).
# (patches/ moved to _WRITABLE_DIRS per D-104 — it was over-restricted by C1 vs D-099③.)
_READONLY_DIRS = {"source"}

# File extensions that get a +1 risk bump inside output_code/
_EXECUTABLE_EXTS = {
    ".py", ".sh", ".bash", ".js", ".ts", ".mjs", ".cjs",
    ".rb", ".pl", ".php", ".go", ".rs",
}


class WorkspaceMediator:
    """Stateless mediator — validates read/write requests from ANY write actor.

    Applies to platform-internal workers (P4 execution worker, Tools) and external
    platforms (OpenCode) alike (D-099).  All methods raise ValueError with a clear
    reason on rejection so callers can surface the denial (back to OpenCode via ACP,
    or to the P4 worker) and write an Audit record.
    """

    def __init__(self, workspace_root: str) -> None:
        self._root = Path(workspace_root).resolve()

    # ── Read ──────────────────────────────────────────────────────────────

    def guard_read(self, path_str: str) -> Path:
        """Check that a read path is inside the workspace.

        Returns the resolved absolute path on success.
        Raises ValueError on boundary violation.
        """
        target = self._resolve(path_str)
        self._assert_inside(target, "read")
        return target

    # ── Write ─────────────────────────────────────────────────────────────

    def check_write(self, path_str: str) -> tuple[Path, str]:
        """Validate a write path and return (resolved_path, risk_level).

        Raises ValueError if the write is unconditionally rejected:
          - path traversal / outside workspace
          - target is inside a read-only directory
          - target is outside the allowed external-write directories
        """
        target = self._resolve(path_str)
        self._assert_inside(target, "write")

        # Determine which top-level dir the target is under
        top_dir = self._top_dir(target)

        if top_dir in _READONLY_DIRS:
            raise ValueError(
                f"Write rejected: {path_str!r} is inside read-only directory '{top_dir}/' "
                "(source/ is read-only for ALL actors — platform-internal "
                "and external, D-099①)."
            )

        if top_dir not in _WRITABLE_DIRS:
            raise ValueError(
                f"Write rejected: {path_str!r} (top-level dir '{top_dir}/') is not in "
                f"the allowed write directories {sorted(_WRITABLE_DIRS)}. "
                "All actors must write to output_code/, artifacts/ or patches/ (D-099/D-104)."
            )

        risk = self._write_risk(target, top_dir)
        return target, risk

    # ── Internal ─────────────────────────────────────────────────────────

    def _resolve(self, path_str: str) -> Path:
        """Resolve path relative to workspace root, or as absolute."""
        p = Path(path_str)
        if not p.is_absolute():
            p = self._root / p
        return p.resolve()

    def _assert_inside(self, target: Path, op: str) -> None:
        """Raise ValueError if target is outside the workspace root."""
        try:
            target.relative_to(self._root)
        except ValueError:
            raise ValueError(
                f"Boundary violation ({op}): {target} is outside workspace root {self._root}."
            )

    def _top_dir(self, target: Path) -> str:
        """Return the top-level directory name relative to workspace root."""
        try:
            rel = target.relative_to(self._root)
            return rel.parts[0] if rel.parts else ""
        except ValueError:
            return ""

    def _write_risk(self, target: Path, top_dir: str) -> str:
        """Compute write risk level for Audit/HITL decisions."""
        # L3: executable/script file types
        if target.suffix.lower() in _EXECUTABLE_EXTS and top_dir == "output_code":
            return "L3"
        # L2: writing to artifacts/ or patches/, or overwriting an existing output_code/ file
        if top_dir in ("artifacts", "patches"):
            return "L2"
        if top_dir == "output_code" and target.exists():
            return "L2"
        # L1: new file in output_code/
        return "L1"


def _workspace_mediator_for(project_id: str) -> WorkspaceMediator:
    """Convenience factory — resolves workspace root from settings."""
    from app.core.config import settings
    from app.services.workspace_service import workspace_path
    return WorkspaceMediator(str(workspace_path(project_id)))
