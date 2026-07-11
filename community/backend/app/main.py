"""Community service FastAPI app (R15-4-C3).

Read-only community content service (news/resources/models/evaluations).
POST /resources/{id}/package is reserved for a future publish side — R15 only
imports from 发布侧 seed, not operated (Q-R15-10).

All list endpoints source data from the independent community SQLite (real
DB, not hardcoded static cards) — satisfying the red line "不得用 mock 数据
冒充 community-backend API".
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.models import (
    CommunityResource, CommunityDoc, CommunityNews, CommunityModelEntry,
    CommunityEvaluation,
)
from app import seed
from app import schemas

app = FastAPI(title="rebuild community", version=seed.VERSION,
                docs_url=None, redoc_url=None)  # free /docs for content docs (R16-E1)

COMMUNITY_VERSION = seed.VERSION  # R16-E1: bumped to reflect docs addition


@app.on_event("startup")
def _startup() -> None:
    init_db()
    seed.seed_on_startup()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── GET /status ────────────────────────────────────────────────────────
@app.get("/status", response_model=schemas.StatusResponse)
def status(db: Session = Depends(get_db)) -> schemas.StatusResponse:
    return schemas.StatusResponse(
        status="ok",
        version=COMMUNITY_VERSION,
        resource_count=db.query(CommunityResource).count(),
        model_count=db.query(CommunityModelEntry).count(),
        evaluation_count=db.query(CommunityEvaluation).count(),
        doc_count=db.query(CommunityDoc).count(),
    )


# ── GET /docs  (R16-E1: community online documentation) ────────────────
@app.get("/docs", response_model=schemas.CommunityDocListResponse)
def docs_list(
    category: str | None = None,
    db: Session = Depends(get_db),
) -> schemas.CommunityDocListResponse:
    q_ = db.query(CommunityDoc).order_by(CommunityDoc.updated_at.desc())
    if category:
        q_ = q_.filter(CommunityDoc.category == category)
    items = q_.all()
    return schemas.CommunityDocListResponse(
        docs=[schemas.CommunityDocItem.model_validate(d) for d in items],
        total=len(items),
    )


@app.get("/docs/{slug}", response_model=schemas.CommunityDocDetail)
def docs_detail(slug: str, db: Session = Depends(get_db)) -> schemas.CommunityDocDetail:
    item = db.get(CommunityDoc, slug)
    if item is None:
        raise HTTPException(status_code=404, detail="Doc not found")
    return schemas.CommunityDocDetail.model_validate(item)


# ── GET /news ──────────────────────────────────────────────────────────
@app.get("/news", response_model=schemas.NewsResponse)
def news(db: Session = Depends(get_db)) -> schemas.NewsResponse:
    items = (db.query(CommunityNews)
             .order_by(CommunityNews.pinned.desc(), CommunityNews.published_at.desc())
             .all())
    return schemas.NewsResponse(news=[schemas.NewsItem.model_validate(i) for i in items])


# ── GET /resources ─────────────────────────────────────────────────────
@app.get("/resources", response_model=schemas.ResourceListResponse)
def resources(
    q: str | None = None,
    type: str | None = None,
    tag: str | None = None,
    source: str | None = None,       # official | community
    offset: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> schemas.ResourceListResponse:
    query = db.query(CommunityResource)
    if type:
        query = query.filter(CommunityResource.resource_type == type)
    if source:
        query = query.filter(CommunityResource.source == source)
    if tag:
        # tag membership check (stored as JSON list)
        query = query.filter(CommunityResource.tags.contains(tag))  # type: ignore[arg-type]
    if q:
        like = f"%{q}%"
        query = query.filter((CommunityResource.name.ilike(like))  # type: ignore[arg-type]
                               | (CommunityResource.display_name.ilike(like))
                               | (CommunityResource.description.ilike(like)))
    total = query.count()
    items = (query.order_by(CommunityResource.download_count.desc())
             .offset(offset).limit(size).all())
    return schemas.ResourceListResponse(
        totalSize=total,
        offset=offset,
        resources=[schemas.ResourceCard.model_validate(i) for i in items],
    )


# ── GET /resources/{id} ───────────────────────────────────────────────
@app.get("/resources/{resource_id}", response_model=schemas.ResourceDetail)
def resource_detail(resource_id: str, db: Session = Depends(get_db)) -> schemas.ResourceDetail:
    item = db.get(CommunityResource, resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return schemas.ResourceDetail.model_validate(item)


# ── R16-B E3: GET /resources/{id}/versions ────────────────────────────
@app.get("/resources/{resource_id}/versions", response_model=schemas.VersionListResponse)
def resource_versions(resource_id: str, db: Session = Depends(get_db)) -> schemas.VersionListResponse:
    """List available versions of a community resource.

    Honesty note (Q-R16B-2 方案 A): community seed only stores ONE real checksum/files
    for the *current* version. Historical versions are recorded here for traceability
    only — importing any old version will download+verify the CURRENT package and warn
    the user. We do NOT pretend to have per-version binaries.
    """
    item = db.get(CommunityResource, resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    current = item.version
    v_list = []
    for v in (item.versions or []):
        v_list.append(schemas.CommunityResourceVersion(
            version=v,
            manifest_ref=f"/resources/{resource_id}/versions/{v}/manifest" if v == current else "",
            deprecated=(v != current),
        ))
    return schemas.VersionListResponse(
        resource_id=item.id,
        versions=v_list,
        current_version=current,
        note="当前仅支持导入最新版本；历史版本记录用于版本追溯。",
    )


@app.get("/resources/{resource_id}/versions/{version}/manifest", response_model=schemas.ManifestResponse)
def resource_version_manifest(resource_id: str, version: str, db: Session = Depends(get_db)) -> schemas.ManifestResponse:
    """Per-version manifest. Only the current version has real files/checksum; others 404."""
    item = db.get(CommunityResource, resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if version != item.version:
        raise HTTPException(status_code=404, detail="仅当前版本有有效的 manifest/files/checksum")
    files = [
        schemas.ManifestFile(name=f.get("name", ""), size=f.get("size", 0))
        for f in (item.files or [])
    ]
    return schemas.ManifestResponse(
        resource_id=item.id,
        version=item.version,
        type=item.resource_type,
        files=files,
        checksum_sha256=item.checksum_sha256,
        dependencies=item.dependencies or [],
    )


# ── GET /resources/{id}/manifest ───────────────────────────────────────
@app.get("/resources/{resource_id}/manifest", response_model=schemas.ManifestResponse)
def resource_manifest(resource_id: str, db: Session = Depends(get_db)) -> schemas.ManifestResponse:
    item = db.get(CommunityResource, resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    files = [
        schemas.ManifestFile(name=f.get("name", ""), size=f.get("size", 0))
        for f in (item.files or [])
    ]
    return schemas.ManifestResponse(
        resource_id=item.id,
        version=item.version,
        type=item.resource_type,
        files=files,
        checksum_sha256=item.checksum_sha256,
        dependencies=item.dependencies or [],
    )


# ── GET /resources/{id}/download ───────────────────────────────────────
@app.get("/resources/{resource_id}/download")
def resource_download(resource_id: str, db: Session = Depends(get_db)) -> FileResponse:
    item = db.get(CommunityResource, resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    pkg = item.package_path
    if not pkg or not Path(pkg).exists():
        raise HTTPException(status_code=404, detail="Package not available")
    # Bump download_count once per successful handoff.
    item.download_count = (item.download_count or 0) + 1
    item.updated_at = _now()
    db.commit()
    return FileResponse(
        path=pkg,
        filename=f"{item.id}.zip",
        media_type="application/zip",
        headers={"X-Checksum-SHA256": item.checksum_sha256 or ""},
    )


# ── GET /models ────────────────────────────────────────────────────────
@app.get("/models", response_model=schemas.ModelListResponse)
def models_list(
    provider: str | None = None,
    family: str | None = None,
    availability: str | None = None,
    task: str | None = None,
    offset: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> schemas.ModelListResponse:
    query = db.query(CommunityModelEntry)
    if provider:
        query = query.filter(CommunityModelEntry.provider_id == provider)
    if family:
        query = query.filter(CommunityModelEntry.family == family)
    if availability:
        query = query.filter(CommunityModelEntry.availability_status == availability)
    if task:
        query = query.filter(CommunityModelEntry.task_tags.contains(task))  # type: ignore[arg-type]
    total = query.count()
    items = query.offset(offset).limit(size).all()
    return schemas.ModelListResponse(
        totalSize=total,
        offset=offset,
        models=[schemas.ModelEntry.model_validate(i) for i in items],
    )


# ── GET /evaluations ───────────────────────────────────────────────────
@app.get("/evaluations", response_model=schemas.EvaluationListResponse)
def evaluations(
    model_id: str | None = None,
    task_type: str | None = None,
    scenario: str | None = None,
    db: Session = Depends(get_db),
) -> schemas.EvaluationListResponse:
    query = db.query(CommunityEvaluation)
    if model_id:
        query = query.filter(CommunityEvaluation.model_id == model_id)
    if task_type:
        query = query.filter(CommunityEvaluation.task_type == task_type)
    if scenario:
        query = query.filter(CommunityEvaluation.scenario == scenario)
    items = query.all()
    return schemas.EvaluationListResponse(
        evaluations=[schemas.EvaluationItem.model_validate(i) for i in items],
    )
