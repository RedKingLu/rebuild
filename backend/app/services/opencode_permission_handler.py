"""OpenCode permission policy engine (D-076 / D-079 / D-088②).

Evaluates each permission request raised by opencode during a task and
returns either "once" (allow) or "reject".  Logic mirrors the platform's
ToolRiskLevel tiers (L0-L5) and reuses DENY_SUBSTRINGS / ALLOWED_COMMANDS
from ExecutionProvider so the two subsystems stay consistent.

R9-5-5: Write decisions now go through WorkspaceMediator (D-088②) so that
external platforms can only write to output_code/ or artifacts/, not source/.
"""

from __future__ import annotations

from pathlib import Path

from app.services.execution_provider import ALLOWED_COMMANDS, DENY_SUBSTRINGS

# Permission type aliases that opencode may use across API versions
_READ_TYPES = {"file_read", "read"}
_WRITE_TYPES = {"file_write", "write"}
_EXEC_TYPES = {"bash", "execute", "shell", "command"}


class PermissionPolicy:
    """Stateless policy: returns "once" or "reject" for each permission request."""

    def decide(
        self,
        perm_type: str,
        target: str,
        workspace_path: str,
    ) -> str:
        """Evaluate a single permission request.

        Args:
            perm_type: OpenCode permission type string (e.g. "file_write", "bash").
            target: The file path or command string being requested.
            workspace_path: Absolute path to the project workspace root.

        Returns:
            "once"   — allow this specific request
            "reject" — deny this request
        """
        pt = perm_type.lower().strip()

        if pt in _READ_TYPES:
            return "once"

        if pt in _WRITE_TYPES:
            return self._decide_write(target, workspace_path)

        if pt in _EXEC_TYPES:
            return self._decide_exec(target)

        # Unknown permission type — safe default is reject
        return "reject"

    # ── Internal helpers ──────────────────────────────────────────────────

    def _decide_write(self, path_str: str, workspace_path: str) -> str:
        """Allow writes only to output_code/ or artifacts/ (D-088② via WorkspaceMediator)."""
        try:
            from app.services.workspace_mediator import WorkspaceMediator
            mediator = WorkspaceMediator(workspace_path)
            mediator.check_write(path_str)  # raises ValueError on any rejection
            return "once"
        except ValueError:
            return "reject"
        except Exception:
            return "reject"

    def _decide_exec(self, command: str) -> str:
        """Allow only safe, pre-approved commands; block dangerous patterns."""
        cmd_lower = command.lower()
        for pattern in DENY_SUBSTRINGS:
            if pattern.lower() in cmd_lower:
                return "reject"
        # Allow if first word is in the explicit allow-list
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word in ALLOWED_COMMANDS:
            return "once"
        # Anything else — reject (secure default; broader allow-list in future)
        return "reject"
