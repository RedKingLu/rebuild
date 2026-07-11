"""Online Source Provider — community resource retrieval for the Agent (R15-4-C5).

Two layers:
  1. REAL retrieval via the Community Connector (community-backend, R15-4-C4).
     `search_community_resources()` queries the independent community service and
     returns live resource cards. This is the primary path — no more static_R15
     cards masquerading as real results.
  2. FALLBACK static cards (tagged `static_R15_fallback`) only when the community
     service is unreachable. Honest degradation: the fallback is clearly tagged
     and the `community_available` flag tells callers which layer served the data.

Public API:
  search_community_resources(query, type, source, limit) -> {items, community_available}
  get_static_online_resources(resource_types, stage) -> list[dict]   # legacy fallback
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("rebuild.online_source_provider")

# Legacy static cards — kept ONLY as an honest fallback when the community
# service is offline. Tagged `static_R15_fallback` (distinct from the old
# `static_R15`) so callers can tell fallback data from real data.
_STATIC_ONLINE_CARDS = [
    {
        "resource_id": "online:ecc-security-guide",
        "name": "ECC Security Guide",
        "resource_type": "knowledge",
        "description": "11 条 Agent 安全基线（隔离/最小权限/审批边界/可观测/Kill Switch）",
        "url": "https://github.com/affaan-m/ECC/blob/main/the-security-guide.md",
        "online_source": "static_R15_fallback",
    },
    {
        "resource_id": "online:ecc-coding-standards",
        "name": "ECC Coding Standards",
        "resource_type": "skill",
        "description": "ECC 编码标准——测试优先/安全优先/不可变性/计划先行",
        "url": "https://github.com/affaan-m/ECC",
        "online_source": "static_R15_fallback",
    },
    {
        "resource_id": "online:ecc-context-budget",
        "name": "ECC Context Budget",
        "resource_type": "skill",
        "description": "上下文预算管理 Skill（ECC P 系列）",
        "url": "https://github.com/affaan-m/ECC/tree/main/skills/context-budget",
        "online_source": "static_R15_fallback",
    },
    {
        "resource_id": "online:ecc-council",
        "name": "ECC Council Pattern",
        "resource_type": "skill",
        "description": "多 Agent 理事会协作模式（ECC P 系列）",
        "url": "https://github.com/affaan-m/ECC/tree/main/skills/council",
        "online_source": "static_R15_fallback",
    },
    {
        "resource_id": "online:ecc-pre-commit-hook",
        "name": "ECC Pre-commit Hook",
        "resource_type": "hook",
        "description": "提交前质量检查 Hook（lint/secret/console.log，warn 模式）",
        "url": "https://github.com/affaan-m/ECC",
        "online_source": "static_R15_fallback",
    },
    {
        "resource_id": "online:ecc-onboarding-skill",
        "name": "ECC Onboarding Skill",
        "resource_type": "skill",
        "description": "代码库 Onboarding 技能（ECC 来源）",
        "url": "https://github.com/affaan-m/ECC/tree/main/skills/codebase-onboarding",
        "online_source": "static_R15_fallback",
    },
]


def search_community_resources(
    query: str | None = None,
    type: str | None = None,
    source: str | None = None,
    limit: int = 10,
) -> dict:
    """REAL community retrieval via the Community Connector (primary path).

    Returns {"items": [...], "community_available": bool}. When the community
    service is unreachable, falls back to the legacy static cards (clearly
    tagged) and sets community_available=False — never fabricates a success.
    """
    try:
        from app.services.community_connector import search_resources
        data = search_resources(q=query, type=type, source=source, size=limit)
        items = []
        for c in data.get("resources", []):
            items.append({
                "resource_id": c.get("id"),
                "name": c.get("name") or c.get("id"),
                "display_name": c.get("display_name") or c.get("name"),
                "resource_type": c.get("resource_type", "knowledge"),
                "description": c.get("description", ""),
                "version": c.get("version", ""),
                "tags": c.get("tags") or [],
                "categories": c.get("categories") or [],
                "license": c.get("license", ""),
                "source": c.get("source", "community"),
                "verified": c.get("verified", False),
                "download_count": c.get("download_count", 0),
                "icon_url": c.get("icon_url"),
                "checksum_sha256": c.get("checksum_sha256", ""),
                "online_source": "community_real",
            })
        return {"items": items, "community_available": True, "total": data.get("totalSize", len(items))}
    except Exception as e:
        # Honest degradation → legacy fallback, clearly flagged.
        logger.warning("community retrieval unreachable (%s); falling back to static cards", e)
        items = _STATIC_ONLINE_CARDS
        if type:
            items = [c for c in items if c["resource_type"] == type]
        return {"items": items[:limit], "community_available": False, "total": len(items)}


def get_static_online_resources(
    resource_types: Optional[list[str]] = None,
    stage: str = "all",
) -> list[dict]:
    """Legacy static fallback cards (tagged static_R15_fallback).

    Prefer search_community_resources() for real data. This remains for callers
    that explicitly want the offline fallback (e.g. fully air-gapped runs).
    """
    if resource_types:
        return [c for c in _STATIC_ONLINE_CARDS if c["resource_type"] in resource_types]
    return list(_STATIC_ONLINE_CARDS)
