"""MCP API routes — real MCP server management (Phase 12)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.services.mcp_service import MCPService
from app.schemas.common import SuccessEnvelope, Meta

mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])


def get_service(db: Session = Depends(get_session)) -> MCPService:
    return MCPService(db)


@mcp_router.get("")
async def list_mcp(
    limit: int = Query(default=100, le=200),
    offset: int = Query(default=0, ge=0),
    svc: MCPService = Depends(get_service),
):
    servers, total = svc.list_all(limit=limit, offset=offset)
    return SuccessEnvelope(
        data={"servers": [MCPService.to_response(s) for s in servers], "total": total},
        meta=Meta(source_status="real", capability_status="available"),
    )


@mcp_router.get("/{mcp_id}")
async def get_mcp(mcp_id: str, svc: MCPService = Depends(get_service)):
    srv = svc.get(mcp_id)
    if not srv:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return SuccessEnvelope(
        data=MCPService.to_response(srv),
        meta=Meta(source_status="real", capability_status="available"),
    )


@mcp_router.post("", status_code=status.HTTP_201_CREATED)
async def create_mcp(data: dict, svc: MCPService = Depends(get_service)):
    srv = svc.create(data)
    return SuccessEnvelope(
        data=MCPService.to_response(srv),
        meta=Meta(source_status="real", capability_status="available"),
    )


@mcp_router.put("/{mcp_id}")
async def update_mcp(mcp_id: str, data: dict, svc: MCPService = Depends(get_service)):
    srv = svc.update(mcp_id, data)
    if not srv:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return SuccessEnvelope(
        data=MCPService.to_response(srv),
        meta=Meta(source_status="real", capability_status="available"),
    )


@mcp_router.delete("/{mcp_id}")
async def delete_mcp(mcp_id: str, svc: MCPService = Depends(get_service)):
    if not svc.delete(mcp_id):
        raise HTTPException(status_code=404, detail="MCP server not found")
    return SuccessEnvelope(
        data={"removed": True},
        meta=Meta(source_status="real", capability_status="available"),
    )


@mcp_router.post("/{mcp_id}/test")
async def test_mcp(mcp_id: str, svc: MCPService = Depends(get_service)):
    """Test MCP connection — spawns real subprocess for stdio transport."""
    result = await svc.test_connection(mcp_id)
    return SuccessEnvelope(
        data=result,
        meta=Meta(source_status="real", capability_status="available"),
    )
