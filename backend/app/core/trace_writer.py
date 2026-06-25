"""Trace writer — FastAPI middleware that records every API request as an in-memory Trace.

R4: Traces are stored in-memory (volatile). They are lost on restart.
This is the minimal engineering landing of D-066 "No Trace, No Trusted Result".

The trace_writer is NOT a persistence layer. It is explicitly volatile.
"""

import time
import uuid
from collections import deque
from typing import Dict, List, Optional

MAX_TRACES = 10000  # in-memory cap to prevent unbounded growth


class TraceWriter:
    """In-memory trace event store. Volatile — not persisted to disk."""

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
        """Record a trace event. Returns the trace dict."""
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
            "persistence": "volatile",
            "created_at": _now(),
            **extra,
        }
        self._traces.append(trace)
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
