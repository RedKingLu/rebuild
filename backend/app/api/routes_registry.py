"""Resource Registry API routes — unified resource CRUD + query."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.registry import (
    ResourceCreate, ResourceUpdate, ResourceResponse,
    ResourceListData, RegistrySummary,
)
from app.services.registry_service import RegistryService
from app.schemas.common import SuccessEnvelope, Meta

registry_router = APIRouter(prefix="/resources", tags=["resources"])


def get_service(db: Session = Depends(get_db)) -> RegistryService:
    return RegistryService(db)


@registry_router.get("")
def list_resources(
    type: str | None = None,
    source: str | None = None,
    trust: str | None = None,
    risk: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, le=200),
    offset: int = Query(default=0, ge=0),
    svc: RegistryService = Depends(get_service),
):
    entries, total = svc.list_all(
        resource_type=type,
        source_type=source,
        trust_level=trust,
        risk_level=risk,
        status=status,
        limit=limit,
        offset=offset,
    )
    return SuccessEnvelope(data=ResourceListData(
        resources=[ResourceResponse(**svc.to_response(e)) for e in entries],
        total=total,
    ), meta=Meta())


@registry_router.get("/registry")
def registry_summary(svc: RegistryService = Depends(get_service)):
    return SuccessEnvelope(data=RegistrySummary(**svc.summary()), meta=Meta())


@registry_router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    entry = svc.get(resource_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return ResourceResponse(**svc.to_response(entry))


@registry_router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
def create_resource(data: ResourceCreate, svc: RegistryService = Depends(get_service)):
    entry = svc.create(data)
    return ResourceResponse(**svc.to_response(entry))


@registry_router.put("/{resource_id}", response_model=ResourceResponse)
def update_resource(resource_id: str, data: ResourceUpdate, svc: RegistryService = Depends(get_service)):
    entry = svc.update(resource_id, data)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return ResourceResponse(**svc.to_response(entry))


@registry_router.delete("/{resource_id}", response_model=SuccessEnvelope)
def delete_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    if not svc.delete(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    return SuccessEnvelope(success=True, detail="Resource deleted")


# ── Review state machine (T6.2 / D-061) ─────────────────────────────────

class ReviewRequest(BaseModel):
    decision: str          # "approve" | "reject"
    reviewer: str = ""
    review_notes: str = ""


@registry_router.post("/{resource_id}/review", response_model=SuccessEnvelope)
def review_resource(
    resource_id: str,
    body: ReviewRequest,
    svc: RegistryService = Depends(get_service),
):
    """Approve or reject a community/user-provided resource.

    approve → status=active, source_trust_level=reviewed_reference
    reject  → status=blocked, source_trust_level=blocked
    """
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 必须是 'approve' 或 'reject'")

    entry = svc.get(resource_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    from datetime import datetime, timezone
    from app.models.resource_entry import ResourceStatus, TrustLevel
    now = datetime.now(timezone.utc)

    if body.decision == "approve":
        entry.status = ResourceStatus.active
        entry.source_trust_level = TrustLevel.reviewed_reference
        entry.review_status = "approved"
    else:
        entry.status = ResourceStatus.blocked
        entry.source_trust_level = TrustLevel.blocked
        entry.review_status = "rejected"

    entry.reviewer = body.reviewer or "platform"
    entry.review_notes = body.review_notes
    entry.updated_at = now
    svc.db.commit()
    svc.db.refresh(entry)

    return SuccessEnvelope(
        data=RegistryService.to_response(entry),
        meta={"review_decision": body.decision, "resource_id": resource_id},
    )


# ── T5.3: knowledge search endpoint ──────────────────────────────────────────

@registry_router.get("/knowledge/search")
def search_knowledge(
    q: str = "",
    scope: str = "all",
    limit: int = Query(default=10, le=50),
    svc: RegistryService = Depends(get_service),
):
    """Full-text search over knowledge resources (T5.3/R9-5-4).

    scope: "all" | "platform" | "user" | "community"
    Returns results with retrieval_mode, snippet, score.
    """
    from app.services.knowledge_search import search
    results = search(q.strip(), svc.db, limit=limit, scope=scope)
    return SuccessEnvelope(
        data={"results": results, "query": q, "scope": scope, "count": len(results)},
        meta={"source_status": "real", "retrieval_mode": results[0]["retrieval_mode"] if results else "fulltext_like"},
    )
