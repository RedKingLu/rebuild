"""SSE / Event API routes.

R4: Real SSE connection + mock event stream.
Uses sse_starlette (validated in V26.0).
"""

import asyncio
import json

from fastapi import APIRouter, Request, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.dependencies import get_services

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
async def event_stream(request: Request, project_id: str | None = None,
                       run_id: str | None = None):
    """SSE event stream — real snapshot (checkpoint state if run_id) + heartbeat.

    No fabricated mock domain events (D-097). A full LangGraph push bus is future work;
    events are emitted while driving via /graph/start|resume.
    """
    svc = get_services()

    async def generate():
        async for event in svc.event_service.event_stream(project_id=project_id, run_id=run_id):
            event_type = event.get("event_type", "message")
            yield {
                "event": event_type,
                "data": json.dumps(event, ensure_ascii=False),
            }
            await asyncio.sleep(0)

    return EventSourceResponse(generate())


@router.get("/{event_id}")
async def get_event(event_id: str):
    """Single event query. Events are not persisted as queryable entities yet —
    honest 404 rather than a fabricated mock event (D-097)."""
    raise HTTPException(404, "事件未持久化为可查询实体（实时事件经 /events/stream）")
