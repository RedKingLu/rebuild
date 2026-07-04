"""Agent Chat SSE route — R9-3G-C: Real streaming agent conversation.

POST /api/projects/{project_id}/agent/chat
Returns text/event-stream with SSE chunks.
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.dependencies import get_services
from app.services.agent_loop import AgentLoop
from app.services.conversation_service import ConversationService, agent_role_for
from app.services.workspace_service import workspace_path

agent_chat_router = APIRouter(prefix="/projects/{project_id}/agent", tags=["agent_chat"])


class AgentChatRequest(BaseModel):
    message: str
    mode: str = "plan"  # manual | plan | auto
    # R9-5-7 T10/T12: set True when resuming after the user approved a parked
    # controlled action via the Gate decision endpoint.
    confirm: bool = False
    # UX-3: explicit conversation id (optional). When omitted, the active conversation
    # for the current stage/specialist is resolved (and created on context switch).
    conversation_id: Optional[str] = None


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

    # UX-3: resolve the specialist agent + active conversation for this stage.
    convo_svc = ConversationService(svc)
    agent_role = agent_role_for(stage)
    convo = convo_svc.get_or_create_active(
        project_id, run_id, stage, agent_role, user_message=req.message,
    )
    history = convo_svc.history_for_agent(convo.conversation_id)
    # Persist the user message up front (so it survives even if the stream is aborted).
    convo_svc.append_message(convo.conversation_id, "user", req.message)

    # Build async generator for SSE streaming
    loop = AgentLoop()
    # Collect the full agent reply + tool events to persist after the stream completes.
    full_content: list[str] = []
    tool_events: list[dict] = []

    async def event_stream():
        nonlocal full_content, tool_events
        # UX-3: tell the frontend which conversation this stream belongs to, so it can
        # bind subsequent messages + refresh the sidebar history list.
        yield ("event: conversation\ndata: "
               + json.dumps({"conversation_id": convo.conversation_id,
                             "agent_role": agent_role, "title": convo.title,
                             "stage": stage}, ensure_ascii=False) + "\n\n")
        try:
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
                agent_type=agent_role,
                history=history,
            ):
                # AgentLoop emits "event: <type>\ndata: {...}\n\n" frames
                etype = frame.split("\n", 1)[0].replace("event: ", "").strip() if frame.startswith("event:") else ""
                # Mirror tool + delta content into the persisted conversation.
                try:
                    import json as _json
                    if etype == "tool":
                        data_line = next((l for l in frame.split("\n") if l.startswith("data: ")), "")
                        if data_line:
                            tool_events.append(_json.loads(data_line[6:]))
                    elif etype == "delta":
                        data_line = next((l for l in frame.split("\n") if l.startswith("data: ")), "")
                        if data_line:
                            d = _json.loads(data_line[6:])
                            if d.get("token"):
                                full_content.append(d["token"])
                except Exception:
                    pass
                yield frame
        finally:
            # UX-3: persist the agent reply + tool events. In `finally` so it runs even if
            # the client disconnects (GeneratorExit) right after the final frame — otherwise
            # the agent reply would be lost (same class of bug as UX-1 call_stream persist).
            agent_text = "".join(full_content)
            if agent_text:
                convo_svc.append_message(convo.conversation_id, "agent", agent_text)
            for te in tool_events:
                convo_svc.append_message(
                    convo.conversation_id, "tool",
                    str(te.get("result", ""))[:500],
                    meta={"tool": te.get("tool", ""), "role": "tool"},
                )
            # Write trace
            svc.trace_writer.write(
                "agent_chat", action="agent_chat",
                summary=f"Agent chat: {req.message[:80]}",
                project_id=project_id,
                extras={"mode": req.mode, "stage": stage, "agent_role": agent_role,
                        "conversation_id": convo.conversation_id},
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


# ── UX-3: conversation history endpoints ──

@agent_chat_router.get("/conversations")
async def list_conversations(project_id: str):
    """List all conversations for a project (most recently active first)."""
    svc = get_services()
    convo_svc = ConversationService(svc)
    return {"conversations": convo_svc.list_by_project(project_id),
            "total": len(convo_svc.list_by_project(project_id))}


class CreateConversationRequest(BaseModel):
    stage: str = ""
    agent_role: str = ""
    title: str = ""


@agent_chat_router.post("/conversations")
async def create_conversation(project_id: str, req: CreateConversationRequest):
    """User-initiated new conversation (the ＋ button)."""
    svc = get_services()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    convo_svc = ConversationService(svc)
    stage = req.stage or (project.current_stage or "p0")
    role = req.agent_role or agent_role_for(stage)
    convo = convo_svc.create_explicit(
        project_id, project.current_run_id or "", stage, role, title=req.title,
    )
    return convo.to_dict()


@agent_chat_router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(project_id: str, conversation_id: str):
    """Replay a conversation's messages (for switching to a past session)."""
    svc = get_services()
    convo_svc = ConversationService(svc)
    convo = convo_svc.get(conversation_id)
    if convo is None or convo.project_id != project_id:
        raise HTTPException(404, "Conversation not found")
    return {"conversation": convo.to_dict(),
            "messages": convo_svc.get_messages(conversation_id)}
