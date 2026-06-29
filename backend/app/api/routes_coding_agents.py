"""Coding Agent API routes — CRUD + availability check + invoke (stub).

D-077 / D-078: Manages external AI coding agent configurations.
Invoke endpoint returns a stub response in R8; R11 will wire to LangGraph.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta
from app.services.coding_agent_service import CodingAgentService

router = APIRouter(prefix="/coding-agents", tags=["coding-agents"])


# ── Request schemas ───────────────────────────────────────────────────────

class CreateCodingAgentRequest(BaseModel):
    agent_type: str = Field(..., description="opencode_cli | qcode_cli | openai_compat | platform_agent")
    name: str = Field(..., min_length=1, max_length=255)
    invoke_mode: str = Field(default="cli", description="cli | api | mcp")
    config: Optional[dict] = Field(default=None, description="Non-sensitive config: model, endpoint, etc.")
    credential_ref: Optional[str] = Field(default=None, description="Reference to Credential table (D-075)")


class UpdateCodingAgentRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    invoke_mode: Optional[str] = None
    config: Optional[dict] = None
    credential_ref: Optional[str] = None
    enabled: Optional[bool] = None


class InvokeCodingAgentRequest(BaseModel):
    task: str = Field(..., min_length=1, description="Natural-language coding task description")
    context_path: str = Field(..., description="Absolute path to project workspace root")


# ── Helper ────────────────────────────────────────────────────────────────

def _agent_to_dict(agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "agent_type": agent.agent_type.value,
        "name": agent.name,
        "invoke_mode": agent.invoke_mode.value,
        "config": agent.config or {},
        "credential_ref": agent.credential_ref,
        "credential_status": "configured" if agent.credential_ref else "not_configured",
        "status": agent.status.value,
        "enabled": agent.enabled,
        "created_at": agent.created_at.isoformat(),
        "updated_at": agent.updated_at.isoformat(),
    }


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("")
async def list_coding_agents(db: Session = Depends(get_db)):
    """List all configured AI coding agents."""
    svc = CodingAgentService(db)
    agents = svc.list_agents()
    return SuccessEnvelope(
        data={"agents": [_agent_to_dict(a) for a in agents], "total": len(agents)},
        meta=Meta(source_status="real", capability_status="available"),
    )


@router.post("")
async def create_coding_agent(req: CreateCodingAgentRequest, db: Session = Depends(get_db)):
    """Create a new AI coding agent configuration."""
    valid_types = {"opencode_cli", "qcode_cli", "openai_compat", "platform_agent"}
    if req.agent_type not in valid_types:
        raise HTTPException(400, f"Invalid agent_type. Must be one of: {sorted(valid_types)}")
    valid_modes = {"cli", "api", "mcp"}
    if req.invoke_mode not in valid_modes:
        raise HTTPException(400, f"Invalid invoke_mode. Must be one of: {sorted(valid_modes)}")

    svc = CodingAgentService(db)
    agent = svc.create(
        agent_type=req.agent_type,
        name=req.name,
        invoke_mode=req.invoke_mode,
        config=req.config,
        credential_ref=req.credential_ref,
    )
    get_services().trace_writer.write(
        "coding_agent_action", action="create_agent",
        summary=f"Created coding agent: {agent.name} ({agent.agent_type.value})",
    )
    return SuccessEnvelope(data=_agent_to_dict(agent), meta=Meta())


@router.get("/{agent_id}")
async def get_coding_agent(agent_id: str, db: Session = Depends(get_db)):
    """Get a single AI coding agent configuration."""
    svc = CodingAgentService(db)
    agent = svc.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Coding agent {agent_id} not found")
    return SuccessEnvelope(data=_agent_to_dict(agent), meta=Meta())


@router.put("/{agent_id}")
async def update_coding_agent(agent_id: str, req: UpdateCodingAgentRequest, db: Session = Depends(get_db)):
    """Update an AI coding agent configuration."""
    svc = CodingAgentService(db)
    updates = req.model_dump(exclude_none=True)
    agent = svc.update(agent_id, **updates)
    if not agent:
        raise HTTPException(404, f"Coding agent {agent_id} not found")
    get_services().trace_writer.write(
        "coding_agent_action", action="update_agent",
        summary=f"Updated coding agent: {agent.name}",
    )
    return SuccessEnvelope(data=_agent_to_dict(agent), meta=Meta())


@router.delete("/{agent_id}")
async def delete_coding_agent(agent_id: str, db: Session = Depends(get_db)):
    """Delete an AI coding agent configuration."""
    svc = CodingAgentService(db)
    ok = svc.delete(agent_id)
    if not ok:
        raise HTTPException(404, f"Coding agent {agent_id} not found")
    get_services().trace_writer.write(
        "coding_agent_action", action="delete_agent",
        summary=f"Deleted coding agent {agent_id}",
    )
    return SuccessEnvelope(data={"deleted": True, "agent_id": agent_id}, meta=Meta())


@router.post("/{agent_id}/test")
async def test_coding_agent(agent_id: str, db: Session = Depends(get_db)):
    """Check CLI/API availability of a configured coding agent."""
    svc = CodingAgentService(db)
    result = svc.check_availability(agent_id)
    if "Agent config not found" in result.get("reason", ""):
        raise HTTPException(404, result["reason"])
    return SuccessEnvelope(data=result, meta=Meta(
        source_status="real",
        capability_status="available" if result["available"] else "not_available",
    ))


@router.post("/{agent_id}/invoke")
async def invoke_coding_agent(agent_id: str, req: InvokeCodingAgentRequest, db: Session = Depends(get_db)):
    """Invoke an AI coding agent with a natural-language task.

    OpenCodeCLIAdapter: real invocation via `opencode run` (LLM_API_KEY from env).
    Platform review agent interception deferred to R11 (D-078).
    """
    svc = CodingAgentService(db)
    if not svc.get(agent_id):
        raise HTTPException(404, f"Coding agent {agent_id} not found")

    result = await svc.invoke(
        agent_id=agent_id,
        task=req.task,
        context_path=req.context_path,
    )
    get_services().trace_writer.write(
        "coding_agent_action", action="invoke_agent",
        summary=f"Invoke coding agent {agent_id}: {req.task[:80]}",
    )
    status = result.get("status", "error")
    is_stub = status == "not_implemented"
    return SuccessEnvelope(data=result, meta=Meta(
        source_status="stub" if is_stub else "real",
        capability_status="not_implemented" if is_stub else ("available" if status == "ok" else "error"),
    ))
