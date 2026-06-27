"""CodingAgentService — CRUD for external AI coding agent configurations.

D-077 / D-078: Manages CodingAgentConfig records.
Real invoke is deferred to R11 (P4 execution chain).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.coding_agent_config import (
    CodingAgentConfig,
    CodingAgentType,
    CodingAgentInvokeMode,
    CodingAgentStatus,
)
from app.services.opencode_adapter import get_coding_agent_adapter


class CodingAgentService:
    def __init__(self, db: Session):
        self.db = db

    # ── CRUD ─────────────────────────────────────────────────────────────

    def list_agents(self) -> list[CodingAgentConfig]:
        return self.db.query(CodingAgentConfig).filter(
            CodingAgentConfig.enabled == True
        ).all()

    def get(self, agent_id: str) -> Optional[CodingAgentConfig]:
        return self.db.query(CodingAgentConfig).filter(
            CodingAgentConfig.agent_id == agent_id
        ).first()

    def create(self, agent_type: str, name: str,
               invoke_mode: str = "cli",
               config: dict | None = None,
               credential_ref: str | None = None) -> CodingAgentConfig:
        agent = CodingAgentConfig(
            agent_type=CodingAgentType(agent_type),
            name=name,
            invoke_mode=CodingAgentInvokeMode(invoke_mode),
            config=config or {},
            credential_ref=credential_ref,
            status=CodingAgentStatus.not_configured,
            enabled=True,
        )
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def update(self, agent_id: str, **kwargs) -> Optional[CodingAgentConfig]:
        agent = self.get(agent_id)
        if not agent:
            return None
        allowed = {"name", "invoke_mode", "config", "credential_ref", "status", "enabled"}
        for key, value in kwargs.items():
            if key in allowed:
                if key == "invoke_mode":
                    value = CodingAgentInvokeMode(value)
                elif key == "status":
                    value = CodingAgentStatus(value)
                setattr(agent, key, value)
        agent.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def delete(self, agent_id: str) -> bool:
        agent = self.get(agent_id)
        if not agent:
            return False
        self.db.delete(agent)
        self.db.commit()
        return True

    # ── Availability check ───────────────────────────────────────────────

    def check_availability(self, agent_id: str) -> dict:
        """Check if the configured agent CLI/API is reachable."""
        agent = self.get(agent_id)
        if not agent:
            return {"available": False, "reason": "Agent config not found"}

        try:
            adapter = get_coding_agent_adapter(agent.agent_type.value)
            available = adapter.is_available()
        except Exception as e:
            available = False

        status = CodingAgentStatus.connected if available else CodingAgentStatus.not_configured
        self.update(agent_id, status=status.value)
        return {
            "available": available,
            "agent_id": agent_id,
            "agent_type": agent.agent_type.value,
            "status": status.value,
        }

    # ── Invoke (Stub — R11 will implement real invocation) ───────────────

    async def invoke(self, agent_id: str, task: str, context_path: str) -> dict:
        """Dispatch a coding task to the external AI agent.

        R8: Returns a stub response. Real invocation via LangGraph deferred to R11.
        R11-TODO: route through LangGraph P4 execution node → CodingAgentAdapter →
                  platform review agent (checks file writes / command execution) → result.
        """
        agent = self.get(agent_id)
        if not agent:
            return {
                "status": "error",
                "reason": "Agent config not found",
                "summary": "",
                "changed_files": [],
                "diff_ref": None,
                "issues": ["Agent config not found"],
                "raw_output": "",
            }

        try:
            adapter = get_coding_agent_adapter(agent.agent_type.value)
        except ValueError as e:
            return {
                "status": "error",
                "reason": str(e),
                "summary": "",
                "changed_files": [],
                "diff_ref": None,
                "issues": [str(e)],
                "raw_output": "",
            }

        # R11-TODO: replace stub call with LangGraph-mediated invocation
        return await adapter.invoke_coding_task(
            task=task,
            context_path=context_path,
            config=agent.config or {},
        )
