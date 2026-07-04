"""ConversationService — UX-3: persistent agent conversation sessions + specialist routing.

A conversation binds (project, run, stage, agent_role). The specialist agent for a given
stage is resolved by agent_role_for(stage): execution/planning stages talk to the Node
Worker, acceptance/rework talks to the Acceptance agent, and Gate decisions talk to the
Conversation/Gate agent. New conversations are started on stage change or on entering
acceptance/rework; the previous one is archived.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.agent_definition import AgentType
from app.models.conversation import Conversation, ChatMessage

logger = logging.getLogger("rebuild.conversation_service")

# Stage → specialist agent role. Execution & planning stages are driven by the Node
# Worker; the acceptance/rework phase is driven by the Acceptance agent; Gate decision
# explanations are driven by the Conversation/Gate agent.
_EXECUTION_STAGES = ["p0", "p1", "p2", "p3", "p4", "p5", "p6"]
_ACCEPTANCE_TRIGGERS = ["acceptance", "rework", "review"]


def agent_role_for(stage: str, phase: str = "") -> str:
    """Resolve the specialist AgentType for the current stage/phase (UX-3 routing)."""
    s = (stage or "p0").lower()
    p = (phase or "").lower()
    if any(t in p or t in s for t in _ACCEPTANCE_TRIGGERS):
        return AgentType.acceptance.value
    if s == "gate" or "gate" in p:
        return AgentType.conversation_gate.value
    if s in _EXECUTION_STAGES:
        return AgentType.node_worker.value
    # Fallback for unknown/future stages: generalist Node Worker.
    return AgentType.node_worker.value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _auto_title(stage: str, agent_role: str, user_message: str) -> str:
    role_label = {
        "node_worker": "执行",
        "acceptance": "验收",
        "conversation_gate": "Gate",
        "auto_review": "审核",
        "expert": "专家",
    }.get(agent_role, agent_role)
    stage_label = (stage or "p0").upper()
    snippet = (user_message or "").strip()[:24]
    return f"{stage_label} · {role_label}对话 · {snippet}" if snippet else f"{stage_label} · {role_label}对话"


class ConversationService:
    def __init__(self, services):
        self._svc = services

    def _db(self) -> Session:
        from app.core.database import get_session
        return get_session()

    # ── Conversation lifecycle ──

    def get_or_create_active(
        self,
        project_id: str,
        run_id: str,
        stage: str,
        agent_role: str,
        *,
        user_message: str = "",
    ) -> Conversation:
        """Return the active conversation for (project, stage, role); create one if none.

        If the active conversation belongs to a different stage/role (e.g. the user moved to
        a new stage or into acceptance), archive it and start a fresh one — this is the
        UX-3 "new session on context switch" boundary.
        """
        db = self._db()
        try:
            active = (
                db.query(Conversation)
                .filter(
                    Conversation.project_id == project_id,
                    Conversation.status == "active",
                )
                .order_by(desc(Conversation.last_message_at), desc(Conversation.created_at))
                .first()
            )
            if active and active.stage == stage and active.agent_role == agent_role:
                return active

            # Context switched — archive the previous active conversation.
            if active:
                active.status = "archived"
                active.last_message_at = active.last_message_at or _now()
                db.flush()

            title = _auto_title(stage, agent_role, user_message)
            conv = Conversation(
                project_id=project_id,
                run_id=run_id or "",
                stage=stage,
                agent_role=agent_role,
                title=title,
                status="active",
                message_count=0,
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
            logger.info(
                "conversation: new active convo %s for project %s stage %s role %s",
                conv.conversation_id, project_id, stage, agent_role,
            )
            return conv
        finally:
            db.close()

    def get(self, conversation_id: str) -> Optional[Conversation]:
        db = self._db()
        try:
            return db.get(Conversation, conversation_id)
        finally:
            db.close()

    def list_by_project(self, project_id: str) -> list[dict]:
        db = self._db()
        try:
            convos = (
                db.query(Conversation)
                .filter(Conversation.project_id == project_id)
                .order_by(desc(Conversation.last_message_at), desc(Conversation.created_at))
                .all()
            )
            return [c.to_dict() for c in convos]
        finally:
            db.close()

    def create_explicit(self, project_id: str, run_id: str, stage: str,
                        agent_role: str, title: str = "") -> Conversation:
        """User-initiated "new conversation" (the ＋ button)."""
        db = self._db()
        try:
            conv = Conversation(
                project_id=project_id,
                run_id=run_id or "",
                stage=stage,
                agent_role=agent_role,
                title=title or f"{(stage or 'p0').upper()} · 新对话",
                status="active",
                message_count=0,
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
            return conv
        finally:
            db.close()

    # ── Messages ──

    def append_message(self, conversation_id: str, role: str, content: str,
                       meta: Optional[dict] = None) -> ChatMessage:
        import json as _json
        db = self._db()
        try:
            conv = db.get(Conversation, conversation_id)
            if conv is None:
                raise ValueError(f"Conversation {conversation_id} not found")
            msg = ChatMessage(
                conversation_id=conversation_id,
                role=role,
                content=content or "",
                meta=_json.dumps(meta or {}, ensure_ascii=False),
            )
            db.add(msg)
            conv.message_count = (conv.message_count or 0) + 1
            conv.last_message_at = _now()
            db.commit()
            db.refresh(msg)
            return msg
        finally:
            db.close()

    def get_messages(self, conversation_id: str) -> list[dict]:
        db = self._db()
        try:
            msgs = (
                db.query(ChatMessage)
                .filter(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.asc())
                .all()
            )
            return [m.to_dict() for m in msgs]
        finally:
            db.close()

    def history_for_agent(self, conversation_id: str) -> list[dict]:
        """Return prior messages as OpenAI-style chat messages for context replay.

        Replays the conversation so the specialist agent remembers the dialogue across
        turns (fixes the multi-round amnesia). System prompt is excluded from replay —
        agent_loop rebuilds it fresh with the specialist persona.
        """
        rows = self.get_messages(conversation_id)
        out = []
        for m in rows:
            role = m["role"]
            if role == "system":
                continue
            if role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.get("meta", {}).get("tool_call_id", ""),
                    "content": m["content"],
                })
            else:
                out.append({"role": role, "content": m["content"]})
        return out
