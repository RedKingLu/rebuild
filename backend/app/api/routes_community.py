"""Community proxy + import routes for the main platform (R15-4-C4/C6).

Exposes a thin surface over the Community Connector:
  - GET  /api/community/resources            search (proxied + filtered)
  - GET  /api/community/resources/{id}        resource detail
  - POST /api/community/resources/{id}/import download + sha256 verify + write ResourceEntry
  - GET  /api/community/models                model catalog
  - GET  /api/community/evaluations           eval results
  - GET  /api/community/news                  community news
  - GET  /api/community/status                connector status (explicit unreachable state)

POST /import is the critical path: it downloads the package, verifies sha256
(ChecksumMismatchError → 409, no silent fallback — P0), then writes a
ResourceEntry with the distribution fields populated (package_url, checksum_sha256,
manifest_json, icon_url, download_count, source_type=community, enabled=True).
POST /introduce is the Agent auto-introduce entry (C5): searches community and,
if the candidate isn't already imported+enabled locally, creates a real Gate.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.registry import ResourceResponse
from app.schemas.common import SuccessEnvelope, Meta
from app.services.registry_service import RegistryService
from app.services.community_connector import (
    search_resources, get_resource, list_models, list_evaluations, list_news,
    status as community_status,
    CommunityUnavailableError, ChecksumMismatchError,
)
from app.services.community_introduction import import_resource, introduce

community_router = APIRouter(prefix="/api/community", tags=["community"])


def _svc(db: Session = Depends(get_db)) -> RegistryService:
    return RegistryService(db)


def _to_response(svc: RegistryService, entry: ResourceEntry) -> dict:
    return ResourceResponse(**svc.to_response(entry)).model_dump()


@community_router.get("/status")
def status_ep() -> dict:
    return SuccessEnvelope(data=community_status(), meta=Meta()).model_dump()


@community_router.get("/resources")
def resources_list(
    q: str | None = None, type: str | None = None,
    source: str | None = None, tag: str | None = None,
    limit: int = 20, offset: int = 0,
) -> dict:
    try:
        data = search_resources(q=q, type=type, source=source, tag=tag,
                                offset=offset, size=min(limit, 100))
    except CommunityUnavailableError as e:
        return SuccessEnvelope(
            data={"totalSize": 0, "offset": offset, "resources": []},
            meta={"available": False, "reason": str(e)},  # honest unreachable state
        ).model_dump()
    return SuccessEnvelope(data=data, meta={"available": True}).model_dump()


@community_router.get("/resources/{resource_id}")
def resource_detail(resource_id: str) -> dict:
    try:
        data = get_resource(resource_id)
    except CommunityUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return SuccessEnvelope(data=data, meta=Meta()).model_dump()


@community_router.post("/resources/{resource_id}/import", response_model=SuccessEnvelope)
def import_resource(resource_id: str, svc: RegistryService = Depends(_svc)) -> SuccessEnvelope:
    """Download + sha256 verify + persist as a community ResourceEntry.

    P0: checksum mismatch → 409 (reject, no fallback success).
    Idempotency: if a ResourceEntry already exists for this community id, reuse it.
    Delegates to the shared community_introduction.import_resource (C4/C5).
    """
    try:
        entry, checksum = import_resource(svc.db, resource_id)
    except CommunityUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ChecksumMismatchError as e:
        raise HTTPException(status_code=409, detail=f"sha256 校验失败，拒绝导入：{e}")
    return SuccessEnvelope(data=_to_response(svc, entry),
                           meta={"detail": "imported", "checksum_sha256": checksum})


class IntroduceRequest(BaseModel):
    query: str | None = None
    resource_type: str | None = None


@community_router.post("/introduce", response_model=SuccessEnvelope)
def introduce_ep(body: IntroduceRequest, svc: RegistryService = Depends(_svc)) -> SuccessEnvelope:
    """Agent auto-introduce entry (C5): search community → one Gate (if not local).

    If the candidate community resource is already imported+enabled locally,
    returns status=available directly (NO second gate). Otherwise creates a real
    community_resource_introduction Gate via the P121 kernel.
    """
    # A representative project context; in graph-driven mode the agent_loop supplies it.
    project_id = "proj-agent-autointro"
    result = introduce(svc.db, query=body.query, type=body.resource_type,
                       project_id=project_id, stage="p4")
    return SuccessEnvelope(data=result, meta={"detail": result["status"]})


@community_router.get("/models")
def models_list(provider: str | None = None, family: str | None = None) -> dict:
    try:
        data = list_models(provider=provider, family=family)
    except CommunityUnavailableError as e:
        return SuccessEnvelope(data={"totalSize": 0, "offset": 0, "models": []},
                               meta={"available": False, "reason": str(e)}).model_dump()
    return SuccessEnvelope(data=data, meta={"available": True}).model_dump()


@community_router.get("/evaluations")
def evaluations_list(model_id: str | None = None, task_type: str | None = None) -> dict:
    try:
        data = list_evaluations(model_id=model_id, task_type=task_type)
    except CommunityUnavailableError as e:
        return SuccessEnvelope(data={"evaluations": []},
                               meta={"available": False, "reason": str(e)}).model_dump()
    return SuccessEnvelope(data=data, meta={"available": True}).model_dump()


@community_router.get("/news")
def news_list() -> dict:
    try:
        items = list_news()
    except CommunityUnavailableError as e:
        return SuccessEnvelope(data={"news": []},
                               meta={"available": False, "reason": str(e)}).model_dump()
    return SuccessEnvelope(data={"news": items}, meta={"available": True}).model_dump()
