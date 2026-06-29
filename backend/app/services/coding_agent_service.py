"""CodingAgentService — CRUD for external AI coding agent configurations.

D-077 / D-078: Manages CodingAgentConfig records.
R9-5-5: invoke now routes through ExternalPlatformDelegator (T3) for OpenCode agents;
        stub types (qcode_cli, platform_agent) remain honestly not_implemented.
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

    # ── Invoke ───────────────────────────────────────────────────────────

    async def invoke(
        self,
        agent_id: str,
        task: str,
        context_path: str,
        *,
        stage: str = "P4",
        mode: str = "plan",
        project_id: str | None = None,
    ) -> dict:
        """Dispatch a coding task to the external AI agent.

        R9-5-5: OpenCode invocations go through ExternalPlatformDelegator (T3)
        which enforces context injection / file mediation / command review / Trace.
        Stub agent types (qcode_cli, platform_agent) return honest not_implemented.
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

        agent_type = agent.agent_type.value

        # ── OpenCode: real delegation via ExternalPlatformDelegator ─────
        if agent_type == CodingAgentType.opencode_cli.value:
            try:
                from app.services.external_platform_delegator import (
                    ExternalPlatformDelegator, DelegationError,
                )
                delegator = ExternalPlatformDelegator(
                    project_id or "",
                    context_path,
                    mode=mode,
                )
                return await delegator.delegate(
                    task,
                    stage=stage,
                    context_policy=(agent.config or {}).get("context_policy", "full"),
                    timeout=int((agent.config or {}).get("timeout_seconds", 300)),
                    agent_config=agent.config or {},
                )
            except DelegationError as exc:
                return {
                    "status": "error",
                    "reason": str(exc),
                    "summary": "",
                    "changed_files": [],
                    "diff_ref": None,
                    "issues": [str(exc)],
                    "raw_output": "",
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "reason": f"Delegation failed: {exc}",
                    "summary": "",
                    "changed_files": [],
                    "diff_ref": None,
                    "issues": [str(exc)],
                    "raw_output": "",
                }

        # ── Stub types: honest not_implemented ───────────────────────────
        try:
            adapter = get_coding_agent_adapter(agent_type)
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

        return await adapter.invoke_coding_task(
            task=task,
            context_path=context_path,
            config=agent.config or {},
        )
