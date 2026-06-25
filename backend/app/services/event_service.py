"""Event service — SSE mock event generator.

R4: Real SSE connection + mock event stream.
11 event domains covered (aligned with future LangGraph event types).
"""

import asyncio
import time
import uuid
from typing import AsyncGenerator


# 11 domain event types for mock stream
MOCK_EVENT_DOMAINS = [
    # domain, event_type, interval_seconds
    ("run", "run.status_changed", 30),
    ("stage", "stage.status_changed", 30),
    ("gate", "gate.created", 60),
    ("task", "task_graph.status_changed", 60),
    ("checkpoint", "checkpoint.created", 120),
    ("interrupt", "interrupt.raised", 120),
    ("resume", "resume.completed", 120),
    ("trace", "trace.written", 60),
    ("audit", "audit.written", 60),
    ("system", "heartbeat", 15),
]


class EventService:
    """Manages SSE mock event generation."""

    def __init__(self, services):
        self._svc = services

    async def event_stream(self, project_id: str | None = None) -> AsyncGenerator[dict, None]:
        """Generate SSE mock events with heartbeat.

        Yields events in the format expected by sse_starlette.
        """
        sequence = 0
        # First event: current state snapshot
        snapshot = _make_event(
            event_type="snapshot",
            project_id=project_id,
            severity="info",
            payload={"message": "SSE connection established — mock event stream (R4)", "project_id": project_id},
        )
        yield snapshot

        # Track per-domain timers
        timers = {d[0]: 0 for d in MOCK_EVENT_DOMAINS}

        while True:
            await asyncio.sleep(1)
            sequence += 1

            for domain, event_type, interval in MOCK_EVENT_DOMAINS:
                timers[domain] += 1
                if timers[domain] >= interval:
                    timers[domain] = 0
                    event = _make_event(
                        event_type=event_type,
                        project_id=project_id,
                        severity="info",
                        sequence=sequence,
                        payload={
                            "domain": domain,
                            "message": f"Mock {event_type} event (R4)",
                            "source_status": "mock",
                            "graph_capability_status": "not_connected",
                        },
                    )
                    yield event

    def make_snapshot(self, project_id: str | None = None) -> dict:
        return _make_event(
            event_type="snapshot",
            project_id=project_id,
            severity="info",
            payload={
                "message": "Current state snapshot (R4 mock)",
                "project_id": project_id,
                "source_status": "mock",
                "graph_capability_status": "not_connected",
                "transition_mode": "mock",
            },
        )


def _make_event(
    event_type: str,
    project_id: str | None = None,
    severity: str = "info",
    sequence: int = 0,
    payload: dict | None = None,
) -> dict:
    return {
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "event_type": event_type,
        "event_version": "1.0",
        "project_id": project_id,
        "source": "mock",
        "severity": severity,
        "sequence": sequence,
        "payload": payload or {},
        "source_status": "mock",
        "graph_capability_status": "not_connected",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
