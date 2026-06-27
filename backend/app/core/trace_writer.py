"""Trace writer — records execution/action traces to in-memory store + file.

R4: In-memory (volatile) store for instant API queries.
R8: Adds file persistence — writes to project workspace .rebuild/traces.jsonl.
    File persistence ensures traces survive restart.
"""

import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

MAX_TRACES = 10000  # in-memory cap to prevent unbounded growth


class TraceWriter:
    """Trace event store — in-memory for API queries + file persistence (R8)."""

    def __init__(self):
        self._traces: deque[dict] = deque(maxlen=MAX_TRACES)

    def write(
        self,
        trace_type: str,
        *,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        action: str = "",
        summary: str = "",
        graph_status: str = "not_connected",
        transition_mode: str = "mock",
        request_id: Optional[str] = None,
        project_id: Optional[str] = None,
        **extra,
    ) -> dict:
        """Record a trace event. Returns the trace dict.

        R8: Also appends to .rebuild/traces.jsonl if project_id is set and workspace exists.
        """
        trace = {
            "trace_id": f"trace-{uuid.uuid4().hex[:12]}",
            "trace_type": trace_type,
            "run_id": run_id,
            "project_id": project_id,
            "stage": stage,
            "action": action,
            "summary": summary,
            "graph_status": graph_status,
            "transition_mode": transition_mode,
            "request_id": request_id,
            "persistence": "file+memory",
            "created_at": _now(),
            **extra,
        }
        self._traces.append(trace)

        # R8: File persistence to project workspace .rebuild/traces.jsonl
        if project_id:
            try:
                from app.core.config import settings
                traces_file = Path(settings.workspace_dir) / "projects" / project_id / ".rebuild" / "traces.jsonl"
                traces_file.parent.mkdir(parents=True, exist_ok=True)
                with open(traces_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(trace, ensure_ascii=False) + "\n")
            except Exception:
                pass  # file persistence failure is non-fatal to in-memory

        return trace

    def query(
        self,
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        trace_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query traces with optional filters (most recent first)."""
        results = list(self._traces)
        if project_id:
            results = [t for t in results if t.get("project_id") == project_id]
        if run_id:
            results = [t for t in results if t.get("run_id") == run_id]
        if stage:
            results = [t for t in results if t.get("stage") == stage]
        if trace_type:
            results = [t for t in results if t.get("trace_type") == trace_type]
        results.reverse()
        return results[:limit]

    def clear(self):
        """Clear all traces (for test isolation)."""
        self._traces.clear()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
