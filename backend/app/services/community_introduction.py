"""Community resource introduction service (R15-4-C5).

Implements the Agent "auto-introduce community resource → one Gate" contract:
  - introduce(): Agent searches the community service for a candidate. If the
    resource is already imported & enabled locally → return it directly (NO second
    gate). Otherwise create a REAL P121/HITL Gate (via GateService) and return
    awaiting_approval. On approve → import via connector (download + sha256
    verify). On reject → flow recovers with next_actions.
  - import_resource(): shared download + sha256 verify + ResourceEntry write,
    used by BOTH the HTTP import route (C4) and the post-approve path here.

Honest degradation: community unreachable / no candidate / checksum mismatch →
explicit status, never a fabricated gate_id or silent success.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.resource_entry import (
    ResourceEntry, ResourceStatus, SourceType, TrustLevel, RiskLevel,
)
from app.services.community_connector import (
    search_resources, get_resource, download_package,
    CommunityUnavailableError, ChecksumMismatchError,
)

logger = logging.getLogger("rebuild.community_introduction")


def _find_local(db: Session, community_id: str) -> ResourceEntry | None:
    stmt = select(ResourceEntry).where(
        ResourceEntry.source_type == SourceType.community,
        ResourceEntry.source_path_or_ref == community_id,
    )
    return db.execute(stmt).scalars().first()


def import_resource(db: Session, community_id: str, version: str | None = None) -> tuple[ResourceEntry, str, str | None]:
    """Download + sha256 verify + persist a community resource (shared path).

    version: R16-B E3 — requested community version. 诚实语义：无论请求哪个版本，
    当前实现始终下载+校验 CURRENT 包（社区 seed 仅对最新版本落库有效），但把
    imported_version 记录为用户请求的版本用于追溯。请求历史版本会在返回的
    meta.warning 中标注（由 route 透传枚举展示）。

    Returns (entry, checksum). Raises CommunityUnavailableError /
    ChecksumMismatchError — callers MUST surface these (P0: no silent fallback).
    Idempotent: re-importing an already-imported resource returns the existing one.
    """
    existing = _find_local(db, community_id)
    meta = get_resource(community_id)
    package_url = f"{settings.community_base_url_normalized}/resources/{community_id}/download"

    # sha256 verify (P0)
    data, checksum = download_package(community_id)

    requested_version = version
    current_version = meta.get("version") or "1.0.0"
    version_warning: str | None = None
    if requested_version and requested_version != current_version:
        version_warning = (f"请求版本 v{requested_version} 与社区当前版本 v{current_version} 不同；已按当前版本落库校验，"
                          f"imported_version 记为 v{requested_version} 用于追溯。")

    if existing is not None:
        # Refresh distribution fields, keep it enabled & usable.
        existing.package_url = package_url
        existing.checksum_sha256 = checksum
        existing.download_count = meta.get("download_count", 0) or 0
        existing.icon_url = meta.get("icon_url")
        existing.enabled = True
        if requested_version:
            existing.imported_version = requested_version
        db.commit()
        db.refresh(existing)
        return existing, checksum, version_warning

    manifest = meta.get("manifest")
    if isinstance(manifest, str):
        try:
            manifest = json.loads(manifest)
        except Exception:
            manifest = None

    entry = ResourceEntry(
        name=meta.get("name") or community_id,
        resource_type=meta.get("resource_type") or "knowledge",
        description=meta.get("description") or "",
        version=meta.get("version") or "1.0.0",
        source_type=SourceType.community,
        source_trust_level=TrustLevel.read_only_reference,
        source_path_or_ref=community_id,
        risk_level=RiskLevel.L0,
        status=ResourceStatus.active,
        enabled=True,
        package_url=package_url,
        checksum_sha256=checksum,
        manifest_json=manifest,
        download_count=meta.get("download_count", 0) or 0,
        icon_url=meta.get("icon_url"),
        capabilities={"community_tags": meta.get("tags") or [],
                      "categories": meta.get("categories") or []},
        imported_version=requested_version,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry, checksum, version_warning


def introduce(
    db: Session,
    *,
    query: str | None = None,
    type: str | None = None,
    project_id: str,
    run_id: str = "",
    stage: str = "p0",
) -> dict:
    """Agent auto-introduce: search community → one Gate (if not already local).

    Returns one of:
      {"status": "available", "resource": {...}}            already imported+enabled → use directly, NO gate
      {"status": "awaiting_approval", "gate_id": ..., "candidate": {...}, "next_actions": [...]}
      {"status": "no_candidate", "query": ...}
      {"status": "community_unreachable", "reason": ...}
    """
    # 1) search community (real)
    try:
        from app.services.online_source_provider import search_community_resources
        res = search_community_resources(query=query, type=type, limit=5)
        items = res.get("items") or []
        community_available = res.get("community_available", False)
        if not items or not community_available:
            # No real candidate, or only a static fallback (社区不可达). The static
            # fallback cards (static_R15_fallback) can never be downloaded/imported, so
            # we must NOT offer a gate that can't be fulfilled — report honestly.
            return {"status": "no_candidate" if not items else "community_unreachable",
                    "query": query, "type": type, "community_available": community_available}
        candidate = items[0]
    except CommunityUnavailableError as e:
        return {"status": "community_unreachable", "reason": str(e)}
    except Exception as e:
        return {"status": "community_unreachable", "reason": str(e)}

    cid = candidate.get("resource_id")
    # 2) already imported & enabled locally? → use directly, NO second gate.
    local = _find_local(db, cid)
    if local is not None and local.enabled:
        return {
            "status": "available",
            "resource_id": local.resource_id,
            "name": local.name,
            "source": "local_community_cache",
            "message": "该社区资源已导入并启用，可直接使用，不再触发审批门。",
        }

    # 3) create a REAL P121/HITL gate (community_resource_introduction).
    try:
        from app.dependencies import get_services
        gs = get_services().gate_service
        gate = gs.create(
            project_id=project_id, run_id=run_id or "", stage=stage,
            gate_type="community_resource_introduction",
            risk_level="L2",
            reason=(f"Agent 自动引入社区资源 {candidate.get('display_name') or cid} "
                    f"（{candidate.get('resource_type')}，来源 community）。首次引入需用户确认。"),
            summary=f"是否允许引入社区资源「{candidate.get('display_name') or cid}」？",
            options=["approve", "reject"],
        )
    except Exception as e:
        logger.warning("community introduction gate create failed: %s", e)
        return {"status": "gate_creation_failed", "reason": str(e), "candidate": candidate}

    return {
        "status": "awaiting_approval",
        "gate_id": gate.gate_id,
        "gate_type": "community_resource_introduction",
        "candidate": candidate,
        "next_actions": [
            "approve → 下载资源包（sha256 校验）并导入为本地社区资源，后续可直接使用",
            "reject → 放弃引入，Agent 继续用现有资源执行",
        ],
    }
