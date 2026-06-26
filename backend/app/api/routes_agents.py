"""Agent Definition API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.schemas.agent import AgentCreate, AgentUpdate, AgentResponse, AgentListData
from app.services.agent_service import AgentService
from app.schemas.common import SuccessEnvelope

agent_router = APIRouter(prefix="/agents", tags=["agents"])


def get_service(db: Session = Depends(get_session)) -> AgentService:
    return AgentService(db)


@agent_router.get("", response_model=AgentListData)
def list_agents(
    agent_type: str | None = None,
    category: str | None = None,
    limit: int = Query(default=100, le=200),
    offset: int = Query(default=0, ge=0),
    svc: AgentService = Depends(get_service),
):
    agents, total = svc.list_all(agent_type=agent_type, category=category, limit=limit, offset=offset)
    return AgentListData(
        agents=[AgentResponse(**svc.to_response(a)) for a in agents],
        total=total,
    )


@agent_router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: str, svc: AgentService = Depends(get_service)):
    agent = svc.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentResponse(**svc.to_response(agent))


@agent_router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(data: AgentCreate, svc: AgentService = Depends(get_service)):
    agent = svc.create(data)
    return AgentResponse(**svc.to_response(agent))


@agent_router.put("/{agent_id}", response_model=AgentResponse)
def update_agent(agent_id: str, data: AgentUpdate, svc: AgentService = Depends(get_service)):
    agent = svc.update(agent_id, data)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentResponse(**svc.to_response(agent))


@agent_router.delete("/{agent_id}", response_model=SuccessEnvelope)
def delete_agent(agent_id: str, svc: AgentService = Depends(get_service)):
    if not svc.delete(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return SuccessEnvelope(success=True, detail="Agent deleted")
