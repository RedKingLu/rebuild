"""Agent Chat SSE route — R9-3G-C: Real streaming agent conversation.

POST /api/projects/{project_id}/agent/chat
Returns text/event-stream with SSE chunks.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.dependencies import get_services
from app.services.agent_loop import AgentLoop
from app.services.workspace_service import workspace_path

agent_chat_router = APIRouter(prefix="/projects/{project_id}/agent", tags=["agent_chat"])


class AgentChatRequest(BaseModel):
    message: str
    mode: str = "plan"  # manual | plan | auto
    # R9-5-7 T10/T12: set True when resuming after the user approved a parked
    # controlled action via the Gate decision endpoint.
    confirm: bool = False


@agent_chat_router.post("/chat")
async def agent_chat(project_id: str, req: AgentChatRequest, request: Request):
    """Streaming agent chat endpoint. Returns SSE (text/event-stream)."""
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # Gather context
    artifacts = []
    profiling_summary = None
    art_dir = workspace_path(project_id) / "artifacts"
    if art_dir.exists():
        artifacts = [f.name for f in art_dir.iterdir() if f.is_file()]

    summary_path = art_dir / "profiling_summary.md"
    if summary_path.exists():
        try:
            profiling_summary = summary_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Recent traces from workspace aggregate
    try:
        aggregate = svc.workspace_service.aggregate(project_id)
        recent_traces = aggregate.get("recent_traces", []) if isinstance(aggregate, dict) else []
    except Exception:
        recent_traces = []

    stage = project.current_stage or "p0"
    project_name = project.name
    run_id = project.current_run_id or ""

    # Count files in source/
    file_count = 0
    source_dir = workspace_path(project_id) / "source"
    if source_dir.exists():
        skip = {".git", "node_modules", "__pycache__"}
        file_count = sum(1 for _ in source_dir.rglob("*") if _.is_file() and not any(p in skip for p in _.parts))

    # Build async generator for SSE streaming
    loop = AgentLoop()

    async def event_stream():
        async for frame in loop.run(
            message=req.message,
            project_id=project_id,
            project_name=project_name,
            stage=stage,
            mode=req.mode,
            run_id=run_id,
            confirm=req.confirm,
            artifacts=artifacts,
            profiling_summary=profiling_summary,
            recent_traces=recent_traces[:10] if recent_traces else None,
            file_count=file_count,
        ):
            # AgentLoop emits "event: <type>\ndata: {...}\n\n" frames
            yield frame

        # Write trace
        svc.trace_writer.write(
            "agent_chat", action="agent_chat",
            summary=f"Agent chat: {req.message[:80]}",
            project_id=project_id,
            extras={"mode": req.mode, "stage": stage},
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
