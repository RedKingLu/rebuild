"""Resource Registry API routes — unified resource CRUD + query."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.registry import (
    ResourceCreate, ResourceUpdate, ResourceResponse,
    ResourceListData, RegistrySummary,
)
from app.services.registry_service import RegistryService
from app.schemas.common import SuccessEnvelope

registry_router = APIRouter(prefix="/resources", tags=["resources"])


def get_service(db: Session = Depends(get_db)) -> RegistryService:
    return RegistryService(db)


@registry_router.get("", response_model=ResourceListData)
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
    return ResourceListData(
        resources=[ResourceResponse(**svc.to_response(e)) for e in entries],
        total=total,
    )


@registry_router.get("/registry", response_model=RegistrySummary)
def registry_summary(svc: RegistryService = Depends(get_service)):
    return RegistrySummary(**svc.summary())


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
