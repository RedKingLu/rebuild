"""OfficialSource — registration seam for official hubs/APIs (R15-4-C11).

This is an INTENTIONALLY EMPTY seam. Rebuild does NOT connect to any real official
API (red line #11). It defines the registry structure and returns a clear
"not_connected" state so the UI can show a placeholder entry without fabricating a
connection. A future phase may activate specific entries, subject to a Gate.

The ResourceEntry / ModelCatalogEntry / CommunityResource models expose an
`icon_url` / `official_icon_url` field so source icons render uniformly via the
SourceBadge component (C6/C7).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OfficialSourceDef:
    name: str
    source_type: str          # "hub" | "api" | "registry"
    base_url: str
    auth_ref: str = ""        # credential reference (unused until activated)
    enabled: bool = False     # seam: all disabled in R15


# Empty registry (seal). No official source is active in R15.
OFFICIAL_SOURCE_REGISTRY: dict[str, OfficialSourceDef] = {}


def list_official_sources() -> list[dict]:
    """Return official sources with explicit not_connected state.

    Each entry is flagged connected=False so the UI never misrepresents an
    unconfigured source as live.
    """
    return [
        {
            "id": k,
            "name": v.name,
            "source_type": v.source_type,
            "base_url": v.base_url,
            "connected": False,
            "state": "not_connected",
        }
        for k, v in OFFICIAL_SOURCE_REGISTRY.items()
    ]


def get_official_source(source_id: str) -> dict | None:
    v = OFFICIAL_SOURCE_REGISTRY.get(source_id)
    if v is None:
        return None
    return {
        "id": source_id,
        "name": v.name,
        "source_type": v.source_type,
        "base_url": v.base_url,
        "connected": False,
        "state": "not_connected",
    }
