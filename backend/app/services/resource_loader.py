"""Resource Loader — resolves a ResourceEntry to a schedulable handle (T1.1 / R9-5-4).

Single entry point for run-time resource dispatch:
  load(resource_id, db) → LoadedResource
  load_available(stage, resource_types, db) → list[LoadedResource]

Dispatch logic:
  tool        → {"schedulable": True, "dispatch": "tool_registry"}
  mcp         → {"schedulable": True, "dispatch": "mcp_call"}
  case        → {"schedulable": True, "dispatch": "case_context", "never_execute": True}
  knowledge   → {"schedulable": True, "dispatch": "knowledge_context"}
  other       → {"schedulable": True, "dispatch": "registry_ref"} (metadata only)
  remote/external source → {"schedulable": False, "reason": "R14_remote_orchestration"}
  blocked trust level → {"schedulable": False, "reason": "blocked:trust_level_blocked"}

注（R15-4-C1 / D-061 修订）：原「community unreviewed → review_required」审核门已移除。
平台内不设资源审核状态机，统一 启用/禁用/软删除 + L1-L5 动作风险；enabled 的社区资源可被直接调度。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.models.resource_entry import (
    ResourceEntry, ResourceStatus, ResourceType, SourceType, TrustLevel,
)

logger = logging.getLogger("rebuild.resource_loader")

# Statuses allowed for dispatch
_SCHEDULABLE_STATUSES = {
    ResourceStatus.active,
    ResourceStatus.read_only,
    ResourceStatus.local_existing,
}

# Source types that are remote / not-yet-supported — return explicit marker
_REMOTE_SOURCE_TYPES = {
    SourceType.external_online,
    SourceType.third_party,
}


@dataclass
class LoadedResource:
    """A resolved, schedulable (or rejected) resource handle."""
    resource_id: str
    name: str
    resource_type: str
    schedulable: bool
    dispatch: str               # "tool_registry" | "mcp_call" | "case_context" | "knowledge_context" | "registry_ref"
    reason: str = ""            # set when schedulable=False
    never_execute: bool = False
    risk_level: str = "L0"
    gate_policy: str = ""
    permission_scope: str = "read_only"
    source_path_or_ref: Optional[str] = None
    type_metadata: dict = field(default_factory=dict)
    description: str = ""
    source_type: str = ""
    entry: Optional[object] = None  # back-ref to ResourceEntry (optional)


def load(resource_id: str, db: Session) -> Optional[LoadedResource]:
    """Resolve a single resource_id to a LoadedResource.

    Returns None if the resource does not exist.
    Returns a LoadedResource with schedulable=False and reason set if the
    resource is not allowed for dispatch (remote, blocked, unreviewed).
    """
    entry: ResourceEntry | None = db.get(ResourceEntry, resource_id)
    if entry is None:
        logger.warning("resource_loader: resource_id=%s not found", resource_id)
        return None
    return _resolve(entry)


def load_available(
    stage: str,
    resource_types: Optional[list[str]] = None,
    db: Session | None = None,
) -> list[LoadedResource]:
    """Load all schedulable resources for a given stage, optionally filtered by type.

    Falls back to empty list gracefully if DB is unavailable.
    """
    if db is None:
        return []
    try:
        q = db.query(ResourceEntry).filter(
            ResourceEntry.enabled == True,
            ResourceEntry.status.in_(list(_SCHEDULABLE_STATUSES)),
        )
        if resource_types:
            rt_vals = [ResourceType(t) for t in resource_types if t in ResourceType._value2member_map_]
            if rt_vals:
                q = q.filter(ResourceEntry.resource_type.in_(rt_vals))
        entries = q.all()
        results = [_resolve(e) for e in entries]
        return [r for r in results if r.schedulable]
    except Exception as e:
        logger.warning("resource_loader.load_available failed: %s", e)
        return []


def _resolve(entry: ResourceEntry) -> LoadedResource:
    """Map a ResourceEntry to a LoadedResource, applying all dispatch rules."""
    base = LoadedResource(
        resource_id=entry.resource_id,
        name=entry.name,
        resource_type=entry.resource_type.value,
        schedulable=True,
        dispatch="registry_ref",
        risk_level=entry.risk_level.value if hasattr(entry.risk_level, "value") else str(entry.risk_level),
        gate_policy=entry.gate_policy or "",
        permission_scope=entry.permission_scope or "read_only",
        source_path_or_ref=entry.source_path_or_ref,
        type_metadata=entry.type_metadata or {},
        description=entry.description or "",
        source_type=entry.source_type.value if hasattr(entry.source_type, "value") else str(entry.source_type),
        entry=entry,
    )

    # ── Rule 1: remote/external source → not schedulable locally (R14)
    if entry.source_type in _REMOTE_SOURCE_TYPES:
        base.schedulable = False
        base.reason = "R14_remote_orchestration"
        base.dispatch = "none"
        return base

    # ── Rule 2: disabled or incompatible status
    if not entry.enabled:
        base.schedulable = False
        base.reason = "disabled"
        base.dispatch = "none"
        return base

    # ── Rule 2b: soft-deleted → not schedulable (R15-4-C1)
    if getattr(entry, "deleted_at", None) is not None:
        base.schedulable = False
        base.reason = "deleted"
        base.dispatch = "none"
        return base

    if entry.status not in _SCHEDULABLE_STATUSES:
        base.schedulable = False
        base.reason = f"status_not_schedulable:{entry.status.value}"
        base.dispatch = "none"
        return base

    # ── Rule 3 (REMOVED, R15-4-C1, D-061 修订执行注 2026-07-09):
    # 原 community unreviewed → review_required 门已移除。社区资源合格性由发布侧保证；
    # 平台内不设资源审核状态机，统一 启用/禁用/软删除（Rule 2/2b）+ L1-L5 动作风险（Rule 4 + tool_registry Gate）。
    # enabled 的社区资源可被 Agent 直接读取；高危动作仍在动作层触发 L1-L5 Gate。

    # ── Rule 4: blocked trust level
    if entry.source_trust_level == TrustLevel.blocked:
        base.schedulable = False
        base.reason = "blocked:trust_level_blocked"
        base.dispatch = "none"
        return base

    # ── Rule 5: dispatch by resource_type
    rt = entry.resource_type
    if rt == ResourceType.tool:
        base.dispatch = "tool_registry"
    elif rt == ResourceType.mcp:
        base.dispatch = "mcp_call"
    elif rt == ResourceType.case:
        base.dispatch = "case_context"
        base.never_execute = True
    elif rt == ResourceType.knowledge:
        base.dispatch = "knowledge_context"
    elif rt in (ResourceType.agent, ResourceType.skill):
        base.dispatch = "agent_skill_ref"
    else:
        base.dispatch = "registry_ref"

    return base
