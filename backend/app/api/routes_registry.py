"""Resource Registry API routes — unified resource CRUD + query."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    """Soft-delete a resource (R15-4-C1): writes deleted_at, does not hard-delete."""
    if not svc.delete(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    return SuccessEnvelope(success=True, detail="Resource deleted")


# ── Enable / Disable (R15-4-C1) ─────────────────────────────────────────
# 平台内社区资源统一 启用/禁用/软删除；不再走 review 状态机。

@registry_router.patch("/{resource_id}/enable", response_model=SuccessEnvelope)
def enable_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    entry = svc.set_enabled(resource_id, True)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return SuccessEnvelope(data=svc.to_response(entry), detail="Resource enabled")


@registry_router.patch("/{resource_id}/disable", response_model=SuccessEnvelope)
def disable_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    entry = svc.set_enabled(resource_id, False)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return SuccessEnvelope(data=svc.to_response(entry), detail="Resource disabled")


# ── Review state machine — DEPRECATED (R15-4-C1, D-061 修订执行注 2026-07-09) ──
# 社区资源合格性由发布侧保证；平台内不设资源审核状态机。approve/reject 退出主链路。
# 端点保留但返回 410 Gone，引导使用 启用/禁用/软删除 + L1-L5 动作风险。

@registry_router.post("/{resource_id}/review")
def review_resource_gone(resource_id: str):
    """DEPRECATED (410 Gone). 社区资源审核门已于 R15-4 废弃（D-061 修订）。

    请改用 PATCH /resources/{id}/enable、PATCH /resources/{id}/disable、
    DELETE /resources/{id}（软删除）。高危动作风险仍由 L1-L5 动作层处理。
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "resource review 状态机已废弃（R15-4，D-061 修订）：社区资源合格性由发布侧保证，"
            "平台内统一 启用/禁用/软删除 + L1-L5 动作风险。请改用 "
            "PATCH /resources/{id}/enable | /disable | DELETE /resources/{id}。"
        ),
    )


# T5.3 knowledge search endpoint moved to routes_knowledge.py (knowledge_router,
# prefix=/knowledge) so its URL is /api/knowledge/search, consistent with
# /api/knowledge/import-package. It was briefly on registry_router (prefix=/resources)
# which produced the misleading /api/resources/knowledge/search path.


def _read_body(entry) -> str | None:
    """Read a knowledge/case resource's Markdown body from its stored body_path."""
    meta = entry.type_metadata or {}
    body_path = meta.get("body_path")
    if body_path:
        from pathlib import Path
        p = Path(body_path)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    # fall back to source_path_or_ref directory's body.md
    ref = entry.source_path_or_ref
    if ref:
        from pathlib import Path
        p = Path(ref)
        candidate = p / "body.md" if p.is_dir() else p.parent / "body.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace")
    return None


# ── T5.3b: knowledge/case content (Markdown body) ────────────────────────

@registry_router.get("/{resource_id}/content")
def resource_content(resource_id: str, svc: RegistryService = Depends(get_service)):
    """Return the Markdown body of a knowledge/case resource (R15-4-C7)."""
    entry = svc.get(resource_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    body = _read_body(entry)
    return SuccessEnvelope(
        data={"resource_id": resource_id, "content": body, "content_type": "text/markdown"},
        meta={"source_status": "real", "has_body": body is not None},
    )
