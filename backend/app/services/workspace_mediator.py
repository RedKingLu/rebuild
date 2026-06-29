"""Workspace mediator — controlled file read/write boundary for external platforms.

D-088② / R9-5-5 T4: External platforms (e.g. OpenCode) MUST access the project
workspace through this mediator.  It enforces:

  - Read: workspace boundary guard (no path traversal, no escape).
  - Write: output directory enforcement (only output_code/ and artifacts/ are writable).
         source/ is READONLY — writes are rejected unconditionally.
  - Risk classification: L0-L3+ based on target directory and file type,
    fed back to the command reviewer / HITL for escalation decisions.

Write risk levels (used by external_command_reviewer):
  L0 — read (no write)
  L1 — write to output_code/ ordinary file (new or overwrite)
  L2 — write to artifacts/ or overwrite an existing output_code/ file with
       the same name (content update)
  L3 — write a script/executable file (.py, .sh, .js, etc.) to output_code/
  L4 — write to any path outside the allowed write dirs but inside workspace
  L5 — path traversal attempt / escape outside workspace root
"""

from __future__ import annotations

import os
from pathlib import Path


# ── Directory-level write policy ─────────────────────────────────────────

# External platforms may ONLY write here (D-088②)
_EXTERNAL_WRITABLE_DIRS = {"output_code", "artifacts"}

# source/ is unconditionally read-only (D-088②)
_READONLY_DIRS = {"source", "patches"}

# File extensions that get a +1 risk bump inside output_code/
_EXECUTABLE_EXTS = {
    ".py", ".sh", ".bash", ".js", ".ts", ".mjs", ".cjs",
    ".rb", ".pl", ".php", ".go", ".rs",
}


class WorkspaceMediator:
    """Stateless mediator — validates read/write requests from external platforms.

    All methods raise ValueError with a clear reason on rejection so callers
    can surface the denial back to OpenCode via ACP and write an Audit record.
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
                "(source/patches/ are read-only for external platforms)."
            )

        if top_dir not in _EXTERNAL_WRITABLE_DIRS:
            raise ValueError(
                f"Write rejected: {path_str!r} (top-level dir '{top_dir}/') is not in "
                f"the allowed write directories {sorted(_EXTERNAL_WRITABLE_DIRS)}. "
                "External platforms must write to output_code/ or artifacts/."
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
        # L2: overwriting an existing file in output_code/, or writing to artifacts/
        if top_dir == "artifacts":
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
