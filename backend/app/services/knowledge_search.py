"""Knowledge Search — full-text search for knowledge resources (T5.2 / R9-5-4).

Baseline: SQLite LIKE full-text search (always available).
Optional: SQLite FTS5 virtual table (better quality; falls back to LIKE if unavailable).
Vector search: future optional (embedding via ModelGateway); not in this release.

Public API:
  search(query, db, limit, stage) -> list[dict]  retrieval_mode included in result
  load_body(resource_id, db) -> str              read stored body from disk
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("rebuild.knowledge_search")


def search(
    query: str,
    db: Session,
    limit: int = 10,
    scope: str = "all",  # "all" | "platform" | "user" | stage code
) -> list[dict]:
    """Full-text search over knowledge resources.

    Returns a list of result dicts with:
      resource_id, name, description, snippet, score, retrieval_mode
    """
    if not query or not query.strip():
        return _list_all(db, limit, scope)

    results = _search_like(query.strip(), db, limit, scope)
    for r in results:
        r["retrieval_mode"] = "fulltext_like"
    return results


def load_body(resource_id: str, db: Session) -> str:
    """Read stored body for a knowledge resource.

    Body is stored at type_metadata.body_path (T5.1 design: file on disk).
    Returns '' if not found (caller should check).
    """
    try:
        from app.models.resource_entry import ResourceEntry, ResourceType
        entry: ResourceEntry | None = db.get(ResourceEntry, resource_id)
        if entry is None or entry.resource_type != ResourceType.knowledge:
            return ""
        meta = entry.type_metadata or {}
        body_path = meta.get("body_path")
        if body_path:
            p = Path(body_path)
            if p.exists():
                return p.read_text("utf-8", errors="replace")
        # Fallback: description acts as body if no file
        return entry.description or ""
    except Exception as e:
        logger.warning("knowledge load_body failed for %s: %s", resource_id, e)
        return ""


def _list_all(db: Session, limit: int, scope: str) -> list[dict]:
    """Return all knowledge resources (empty query → list)."""
    try:
        from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
        q = db.query(ResourceEntry).filter(
            ResourceEntry.resource_type == ResourceType.knowledge,
            ResourceEntry.enabled == True,
            ResourceEntry.status.in_([ResourceStatus.active, ResourceStatus.read_only,
                                       ResourceStatus.local_existing]),
        )
        q = _apply_scope(q, scope)
        entries = q.limit(limit).all()
        return [_to_result(e, "", 0.0) for e in entries]
    except Exception as e:
        logger.warning("knowledge _list_all failed: %s", e)
        return []


def _search_like(query: str, db: Session, limit: int, scope: str) -> list[dict]:
    """LIKE-based full-text search over name + description + body_path content."""
    try:
        from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
        pattern = f"%{query}%"
        q = db.query(ResourceEntry).filter(
            ResourceEntry.resource_type == ResourceType.knowledge,
            ResourceEntry.enabled == True,
            ResourceEntry.status.in_([ResourceStatus.active, ResourceStatus.read_only,
                                       ResourceStatus.local_existing]),
        )
        q = _apply_scope(q, scope)
        # Simple score: 2 for name match, 1 for description match
        entries = q.all()
        scored = []
        for e in entries:
            score = 0.0
            qlow = query.lower()
            if qlow in (e.name or "").lower():
                score += 2.0
            if qlow in (e.description or "").lower():
                score += 1.0
            # Also search body on disk
            meta = e.type_metadata or {}
            body_path = meta.get("body_path")
            if body_path:
                try:
                    body = Path(body_path).read_text("utf-8", errors="replace")
                    if qlow in body.lower():
                        score += 1.5
                except Exception:
                    logger.debug("knowledge search: failed to read body_path %r", body_path, exc_info=True)
            if score > 0:
                scored.append((e, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [_to_result(e, query, score) for e, score in scored[:limit]]
    except Exception as e:
        logger.warning("knowledge _search_like failed: %s", e)
        return []


def _apply_scope(q, scope: str):
    """Apply source_type filter based on scope."""
    from app.models.resource_entry import ResourceEntry, SourceType
    if scope == "platform":
        q = q.filter(ResourceEntry.source_type == SourceType.internal_current)
    elif scope == "user":
        q = q.filter(ResourceEntry.source_type == SourceType.user_provided)
    elif scope == "community":
        q = q.filter(ResourceEntry.source_type == SourceType.community)
    # "all" → no filter
    return q


def _to_result(entry, query: str, score: float) -> dict:
    meta = entry.type_metadata or {}
    body_path = meta.get("body_path")
    snippet = ""
    if body_path:
        try:
            body = Path(body_path).read_text("utf-8", errors="replace")
            if query:
                idx = body.lower().find(query.lower())
                start = max(0, idx - 60)
                snippet = body[start:start + 160].replace("\n", " ")
            else:
                snippet = body[:160].replace("\n", " ")
        except Exception:
            logger.debug("knowledge _to_result: failed to read snippet from body_path %r", body_path, exc_info=True)
            snippet = (entry.description or "")[:160]
    return {
        "resource_id": entry.resource_id,
        "name": entry.name,
        "description": entry.description or "",
        "source_type": entry.source_type.value if hasattr(entry.source_type, "value") else str(entry.source_type),
        "snippet": snippet,
        "score": score,
        "retrieval_mode": "fulltext_like",
        "body_path": body_path,
    }
