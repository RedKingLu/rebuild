"""SSE / Event API routes.

R4: Real SSE connection + mock event stream.
Uses sse_starlette (validated in V26.0).
"""

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.dependencies import get_services

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def event_stream(request: Request, project_id: str | None = None):
    """SSE event stream — mock events for R4.

    First event is a state snapshot. Subsequent events are mock
    domain events at configured intervals. Heartbeat every 15s.
    """
    svc = get_services()

    async def generate():
        async for event in svc.event_service.event_stream(project_id=project_id):
            event_type = event.get("event_type", "message")
            yield {
                "event": event_type,
                "data": json.dumps(event, ensure_ascii=False),
            }
            await asyncio.sleep(0)

    return EventSourceResponse(generate())


@router.get("/{event_id}")
async def get_event(event_id: str):
    """Single event query — returns the stored mock event.

    In R4, events are not persisted beyond the in-memory stream.
    """
    return {
        "event_id": event_id,
        "message": "R4 does not persist events; events are stream-only.",
        "source_status": "mock",
        "persistence": "volatile",
    }
