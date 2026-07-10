"""ModelCatalog API (R15-4-C8/C9): persistent model catalog query + import.

GET  /api/model-catalog          list with filters (provider/family/availability/task)
POST /api/model-catalog/import   import catalog entries (community / bulk)

Serves the ModelsPage catalog tab (C9). Coexists with runtime ModelGateway — this is
a reference catalog, not the invocation path.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.model_catalog import ModelCatalogEntry
from app.schemas.common import SuccessEnvelope, Meta

catalog_router = APIRouter(prefix="/model-catalog", tags=["model-catalog"])


def _to_dict(e: ModelCatalogEntry) -> dict:
    return {
        "catalog_id": e.catalog_id,
        "model_id": e.model_id,
        "provider_id": e.provider_id,
        "display_name": e.display_name,
        "family": e.family,
        "model_version": e.model_version,
        "context_window": e.context_window,
        "max_output_tokens": e.max_output_tokens,
        "input_modalities": e.input_modalities or [],
        "output_modalities": e.output_modalities or [],
        "capability_tags": e.capability_tags or [],
        "task_tags": e.task_tags or [],
        "license": e.license,
        "availability_status": e.availability_status,
        "official_icon_url": e.official_icon_url,
        "official_url": e.official_url,
        "source": e.source,
        "pricing_input": e.pricing_input,
        "pricing_output": e.pricing_output,
        "speed_level": e.speed_level,
        "latency_level": e.latency_level,
        "updated_at": e.updated_at,
    }


@catalog_router.get("")
def list_catalog(
    provider: str | None = None,
    family: str | None = None,
    availability: str | None = None,
    task: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ModelCatalogEntry)
    if provider:
        stmt = stmt.where(ModelCatalogEntry.provider_id == provider)
    if family:
        stmt = stmt.where(ModelCatalogEntry.family == family)
    if availability:
        stmt = stmt.where(ModelCatalogEntry.availability_status == availability)
    if task:
        # task tag membership (JSON list contains)
        stmt = stmt.where(ModelCatalogEntry.task_tags.contains(task))  # type: ignore[arg-type]
    total = db.execute(select(ModelCatalogEntry)).scalars().all()
    items = db.execute(stmt.order_by(ModelCatalogEntry.display_name).offset(offset).limit(limit)).scalars().all()
    return SuccessEnvelope(
        data={"total": len(total), "offset": offset, "models": [_to_dict(e) for e in items]},
        meta=Meta(),
    ).model_dump()


class CatalogImportItem(BaseModel):
    model_id: str
    provider_id: str
    display_name: str = ""
    family: str = ""
    model_version: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    capability_tags: list[str] = []
    task_tags: list[str] = []
    input_modalities: list[str] = []
    output_modalities: list[str] = []
    license: str | None = None
    official_icon_url: str | None = None
    official_url: str | None = None
    availability_status: str = "available"
    source: str = "imported"


class CatalogImportRequest(BaseModel):
    models: list[CatalogImportItem]


@catalog_router.post("/import")
def import_catalog(body: CatalogImportRequest, db: Session = Depends(get_db)) -> dict:
    """Import model catalog entries (e.g. from community model directory)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    imported = 0
    for m in body.models:
        catalog_id = f"{m.provider_id}/{m.model_id}"
        existing = db.get(ModelCatalogEntry, catalog_id)
        if existing is None:
            existing = ModelCatalogEntry(catalog_id=catalog_id, updated_at=now)
            db.add(existing)
        existing.model_id = m.model_id
        existing.provider_id = m.provider_id
        existing.display_name = m.display_name or m.model_id
        existing.family = m.family
        existing.model_version = m.model_version
        existing.context_window = m.context_window
        existing.max_output_tokens = m.max_output_tokens
        existing.capability_tags = m.capability_tags
        existing.task_tags = m.task_tags
        existing.input_modalities = m.input_modalities
        existing.output_modalities = m.output_modalities
        existing.license = m.license
        existing.official_icon_url = m.official_icon_url
        existing.official_url = m.official_url
        existing.availability_status = m.availability_status
        existing.source = m.source
        existing.updated_at = now
        imported += 1
    db.commit()
    return SuccessEnvelope(data={"imported": imported, "total": db.query(ModelCatalogEntry).count()}, meta=Meta()).model_dump()
