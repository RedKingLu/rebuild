"""Audit writer — records Gate decisions and high-risk actions as in-memory Audit entries.

R4: Audits are stored in-memory (volatile). They are lost on restart.
This is the minimal engineering landing of D-034 (Gate decisions MUST be audited).

The audit_writer is NOT a persistence layer. It is explicitly volatile.
"""

import hashlib
import time
import uuid
from collections import deque
from typing import Optional

MAX_AUDITS = 10000  # in-memory cap


class AuditWriter:
    """In-memory audit log. Volatile — not persisted to disk."""

    def __init__(self):
        self._audits: deque[dict] = deque(maxlen=MAX_AUDITS)

    def write(
        self,
        audit_type: str,
        *,
        gate_id: Optional[str] = None,
        risk_level: str = "L0",
        action: str = "",
        decision: str = "",
        reason: str = "",
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        transition_mode: str = "mock",
        **extra,
    ) -> dict:
        """Record an audit entry. Returns the audit dict."""
        audit_id_seed = f"{audit_type}:{gate_id or ''}:{decision}:{_now()}"
        audit = {
            "audit_id": f"AU-{hashlib.sha256(audit_id_seed.encode()).hexdigest()[:10]}",
            "audit_type": audit_type,
            "gate_id": gate_id,
            "risk_level": risk_level,
            "action": action,
            "decision": decision,
            "reason": reason,
            "project_id": project_id,
            "run_id": run_id,
            "stage": stage,
            "transition_mode": transition_mode,
            "persistence": "volatile",
            "created_at": _now(),
            **extra,
        }
        self._audits.append(audit)
        return audit

    def query(
        self,
        project_id: Optional[str] = None,
        gate_id: Optional[str] = None,
        risk_level: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query audits with optional filters (most recent first)."""
        results = list(self._audits)
        if project_id:
            results = [a for a in results if a.get("project_id") == project_id]
        if gate_id:
            results = [a for a in results if a.get("gate_id") == gate_id]
        if risk_level:
            results = [a for a in results if a.get("risk_level") == risk_level]
        results.reverse()
        return results[:limit]

    def list_all(self) -> list[dict]:
        """Return all audit entries (most recent first). For test verification."""
        results = list(self._audits)
        results.reverse()
        return results

    def clear(self):
        """Clear all audits (for test isolation)."""
        self._audits.clear()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
