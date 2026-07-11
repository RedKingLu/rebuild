"""Resource Resolver — three-tier priority search: self → local → online (T7.1 / R9-5-4).

Tier 1 (self): project-specific bindings (agent.bound_skills/bound_tools for this project)
Tier 2 (local): platform Registry (enabled + active + local source)
Tier 3 (online): OnlineSourceProvider (static/留接口, D-089/R15)

Public API:
  resolve(name_or_type, stage, project_id, db) -> ResolvedResource | None
  resolve_many(resource_types, stage, project_id, db) -> list[ResolvedResource]

Hit tier is written to result for traceability (公理5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("rebuild.resource_resolver")


@dataclass
class ResolvedResource:
    resource_id: str
    name: str
    resource_type: str
    hit_tier: str           # "self" | "local" | "online"
    schedulable: bool = True
    dispatch: str = "registry_ref"
    description: str = ""
    source: str = ""        # source_path_or_ref or online source identifier
    metadata: dict = field(default_factory=dict)


def resolve_many(
    resource_types: Optional[list[str]] = None,
    stage: str = "p0",
    project_id: Optional[str] = None,
    db: Optional[Session] = None,
) -> list[ResolvedResource]:
    """Resolve multiple resources across all three tiers.

    Returns all schedulable resources found, each tagged with hit_tier.
    """
    results: list[ResolvedResource] = []

    # Tier 1: self (project-specific bindings)
    self_resources = _resolve_tier_self(project_id, stage, db)
    results.extend(self_resources)
    self_ids = {r.resource_id for r in self_resources}

    # Tier 2: local Registry
    local_resources = _resolve_tier_local(resource_types, stage, db, exclude_ids=self_ids)
    results.extend(local_resources)
    local_ids = {r.resource_id for r in local_resources}

    # Tier 3: online (static provider, D-089/R15)
    online_resources = _resolve_tier_online(resource_types, stage, exclude_ids=self_ids | local_ids)
    results.extend(online_resources)

    return results


def resolve(
    name_or_type: str,
    stage: str = "p0",
    project_id: Optional[str] = None,
    db: Optional[Session] = None,
) -> Optional[ResolvedResource]:
    """Resolve a single resource by name or type. Returns first match (highest tier wins)."""
    all_resources = resolve_many(resource_types=[name_or_type], stage=stage,
                                 project_id=project_id, db=db)
    return all_resources[0] if all_resources else None


# ── Tier implementations ──────────────────────────────────────────────────────

def _resolve_tier_self(
    project_id: Optional[str],
    stage: str,
    db: Optional[Session],
) -> list[ResolvedResource]:
    """Tier 1: project-specific bound resources from agent definition."""
    if not project_id or db is None:
        return []
    try:
        from app.services.agent_selector import select_agent
        agent = select_agent(stage, "default")
        if not agent:
            return []
        results = []
        for ref in (agent.get("bound_skills") or []):
            rid = ref if isinstance(ref, str) else ref.get("skill_id", "")
            if rid:
                results.append(ResolvedResource(
                    resource_id=rid, name=rid, resource_type="skill",
                    hit_tier="self", dispatch="agent_skill_ref",
                ))
        for ref in (agent.get("bound_tools") or []):
            rid = ref if isinstance(ref, str) else ref.get("tool_id", "")
            if rid:
                results.append(ResolvedResource(
                    resource_id=rid, name=rid, resource_type="tool",
                    hit_tier="self", dispatch="tool_registry",
                ))
        return results
    except Exception as e:
        logger.debug("_resolve_tier_self failed: %s", e)
        return []


def _resolve_tier_local(
    resource_types: Optional[list[str]],
    stage: str,
    db: Optional[Session],
    exclude_ids: Optional[set] = None,
) -> list[ResolvedResource]:
    """Tier 2: platform local Registry."""
    if db is None:
        return []
    try:
        from app.services.resource_loader import load_available
        loaded = load_available(stage=stage, resource_types=resource_types, db=db)
        exclude = exclude_ids or set()
        return [
            ResolvedResource(
                resource_id=r.resource_id,
                name=r.name,
                resource_type=r.resource_type,
                hit_tier="local",
                schedulable=r.schedulable,
                dispatch=r.dispatch,
                description=r.description,
                source=r.source_path_or_ref or "",
                metadata=r.type_metadata or {},
            )
            for r in loaded
            if r.resource_id not in exclude
        ]
    except Exception as e:
        logger.debug("_resolve_tier_local failed: %s", e)
        return []


def _resolve_tier_online(
    resource_types: Optional[list[str]],
    stage: str,
    exclude_ids: Optional[set] = None,
) -> list[ResolvedResource]:
    """Tier 3: online community source (R15-4 真实检索 + 静态 fallback)."""
    exclude = exclude_ids or set()
    try:
        from app.services.online_source_provider import search_community_resources
        rtype = resource_types[0] if resource_types else None
        res = search_community_resources(query=None, type=rtype, limit=20)
        items = res.get("items") or []
        return [
            ResolvedResource(
                resource_id=c["resource_id"],
                name=c.get("name") or c["resource_id"],
                resource_type=c.get("resource_type", "knowledge"),
                hit_tier="online",
                schedulable=False,  # online resources require import first (D-089/C4)
                dispatch="online_import_required",
                description=c.get("description", ""),
                source=c.get("url") or c["resource_id"],
                metadata={"online_source": c.get("online_source", "community"),
                          "community_available": res.get("community_available", False)},
            )
            for c in items
            if c.get("resource_id") not in exclude
        ]
    except Exception as e:
        logger.debug("_resolve_tier_online failed: %s", e)
        return []
