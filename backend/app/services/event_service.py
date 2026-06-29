"""Event service — SSE stream of REAL state (R9-5-1 阶段D / D-097).

No mock/fabricated domain events. The stream emits:
  1. an initial real snapshot (checkpointed graph state if run_id given, else project pointer),
  2. periodic heartbeats.

A full push event bus driven by LangGraph execution is a larger feature (graph events are
emitted while DRIVING via /graph/start|resume, not from a passive watch). Until that bus
exists, this stream is honest: real snapshot + heartbeat, never fabricated domain data.
"""

import asyncio
import time
import uuid
from typing import AsyncGenerator, Optional

from app.graph.runtime import get_flow_runtime, graph_capability_probe

HEARTBEAT_SECONDS = 15


class EventService:
    def __init__(self, services):
        self._svc = services

    async def event_stream(self, project_id: Optional[str] = None,
                           run_id: Optional[str] = None) -> AsyncGenerator[dict, None]:
        """Yield a real snapshot then heartbeats (no fabricated domain events, D-097)."""
        yield await self._snapshot(project_id, run_id)
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            yield _make_event("heartbeat", project_id=project_id,
                              payload={"run_id": run_id})

    async def _snapshot(self, project_id: Optional[str], run_id: Optional[str]) -> dict:
        payload: dict = {"project_id": project_id, "run_id": run_id}
        if run_id:
            try:
                snap = await get_flow_runtime().get_state(run_id)
                vals = snap.values or {}
                payload.update({
                    "current_stage": vals.get("current_stage"),
                    "run_status": vals.get("run_status"),
                    "stage_status": vals.get("stage_status", {}),
                    "pending_gate": vals.get("pending_gate"),
                })
            except Exception:
                # honest: no checkpoint for this run yet (not fabricated)
                payload["state"] = "no_checkpoint"
        return _make_event("snapshot", project_id=project_id, payload=payload)


def _make_event(event_type: str, project_id: Optional[str] = None,
                severity: str = "info", sequence: int = 0,
                payload: Optional[dict] = None) -> dict:
    return {
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "event_type": event_type,
        "event_version": "1.0",
        "project_id": project_id,
        "source": "rebuild",
        "severity": severity,
        "sequence": sequence,
        "payload": payload or {},
        "graph_capability_status": graph_capability_probe(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
