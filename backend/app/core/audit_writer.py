"""Audit writer — records Gate decisions and high-risk actions to in-memory store + file.

R4: In-memory (volatile) store for instant API queries.
R8: Adds file persistence — writes to project workspace .rebuild/audits.jsonl.
    File persistence ensures audits survive restart.
"""

import hashlib
import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

MAX_AUDITS = 10000  # in-memory cap


class AuditWriter:
    """Audit log — in-memory for API queries + file persistence (R8)."""

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
        """Record an audit entry. Returns the audit dict.

        R8: Also appends to .rebuild/audits.jsonl if project_id is set and workspace exists.
        """
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
            "persistence": "file+memory",
            "created_at": _now(),
            **extra,
        }
        self._audits.append(audit)

        # R8: File persistence to project workspace .rebuild/audits.jsonl
        if project_id:
            try:
                from app.core.config import settings
                audits_file = Path(settings.workspace_dir) / "projects" / project_id / ".rebuild" / "audits.jsonl"
                audits_file.parent.mkdir(parents=True, exist_ok=True)
                with open(audits_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(audit, ensure_ascii=False) + "\n")
            except Exception:
                pass  # file persistence failure is non-fatal to in-memory

        return audit

    def query(
        self,
        project_id: Optional[str] = None,
        gate_id: Optional[str] = None,
        risk_level: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query audits with optional filters (most recent first).

        R9 P1-3: Also reads from .rebuild/audits.jsonl so audits survive restart.
        """
        results = list(self._audits)

        # Read persisted audits from jsonl file (survives restart)
        if project_id:
            try:
                from app.core.config import settings
                audits_file = Path(settings.workspace_dir) / "projects" / project_id / ".rebuild" / "audits.jsonl"
                if audits_file.exists():
                    seen_ids = {a.get("audit_id") for a in results}
                    with open(audits_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                a = json.loads(line)
                                aid = a.get("audit_id", "")
                                if aid and aid not in seen_ids:
                                    seen_ids.add(aid)
                                    results.append(a)
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

        if project_id:
            results = [a for a in results if a.get("project_id") == project_id]
        if gate_id:
            results = [a for a in results if a.get("gate_id") == gate_id]
        if risk_level:
            results = [a for a in results if a.get("risk_level") == risk_level]
        results.sort(key=lambda a: a.get("created_at", ""), reverse=True)
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
