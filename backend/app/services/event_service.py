"""Event service — SSE stream of REAL state (R9-5-1 阶段D / D-097 / R17.2 OD-08).

No mock/fabricated domain events. The stream emits:
  1. an initial real snapshot (checkpointed graph state if run_id given, else project pointer),
  2. `state_changed` events derived from REAL diffs of the LangGraph checkpoint snapshot
     (current_stage / run_status / stage_status / pending_gate) discovered by periodic polling,
  3. periodic heartbeats when nothing has changed.

The `state_changed` events are NOT fabricated: each carries the real post-change field values
read back from `get_flow_runtime().get_state(run_id)` (the same checkpoint the graph writes while
driving via /graph/start|resume). When there is no run_id or no checkpoint yet, the stream stays
honest (snapshot state="no_checkpoint") and never emits a state_changed event.
"""

import asyncio
import logging
import time
import uuid
from typing import AsyncGenerator, Optional, Tuple

from app.graph.runtime import get_flow_runtime, graph_capability_probe

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15
POLL_SECONDS = 2  # how often we poll the real checkpoint snapshot for changes

# The real checkpoint fields we diff to detect genuine business progress.
_TRACKED = ("current_stage", "run_status", "stage_status", "pending_gate")


class EventService:
    def __init__(self, services):
        self._svc = services

    async def event_stream(self, project_id: Optional[str] = None,
                           run_id: Optional[str] = None) -> AsyncGenerator[dict, None]:
        """Yield a real snapshot, then real state_changed diffs + heartbeats (D-097)."""
        last, _ok = await self._read_fields(run_id)
        yield self._snapshot(project_id, run_id, last)

        sequence = 0
        since_heartbeat = 0.0
        while True:
            await asyncio.sleep(POLL_SECONDS)
            since_heartbeat += POLL_SECONDS
            fields, ok = await self._read_fields(run_id)

            # Emit a state_changed event ONLY on a real diff of the checkpoint snapshot.
            if ok and fields is not None and fields != last:
                base = last or {}
                changed = [k for k in _TRACKED if fields.get(k) != base.get(k)]
                sequence += 1
                yield _make_event("state_changed", project_id=project_id, sequence=sequence,
                                  payload={"run_id": run_id, "changed": changed, **fields})
                last = fields
                since_heartbeat = 0.0
                continue

            # No real change → keep the connection alive on the heartbeat cadence.
            if since_heartbeat >= HEARTBEAT_SECONDS:
                yield _make_event("heartbeat", project_id=project_id,
                                  payload={"run_id": run_id})
                since_heartbeat = 0.0

    async def _read_fields(self, run_id: Optional[str]) -> Tuple[Optional[dict], bool]:
        """Read the tracked checkpoint fields for run_id.

        Returns (fields, ok):
          - (None, True)  : no run_id, or no checkpoint/state yet (honest, comparable baseline)
          - (None, False) : get_state raised — logged, caller must not treat as a change
          - (dict, True)  : real snapshot fields
        """
        if not run_id:
            return None, True
        try:
            snap = await get_flow_runtime().get_state(run_id)
        except Exception as exc:  # noqa: BLE001 — surfaced via logger.warning (公理3), never silent
            logger.warning("event_stream: get_state failed for run_id=%s: %s", run_id, exc)
            return None, False
        vals = getattr(snap, "values", None) or {}
        if not vals:
            return None, True
        return {k: vals.get(k) for k in _TRACKED}, True

    def _snapshot(self, project_id: Optional[str], run_id: Optional[str],
                  fields: Optional[dict]) -> dict:
        payload: dict = {"project_id": project_id, "run_id": run_id}
        if run_id:
            if fields is not None:
                payload.update(fields)
            else:
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
