"""OfficialSource API (R15-4-C11): empty seam — returns not_connected state.

GET /api/official-sources  → empty list OR not_connected entries (never fabricated live).

No real official API is connected (red line #11). The endpoint exists so the UI can
render an official-sources surface without misrepresenting connectivity. Future
activation of a specific source requires a Gate + auth_ref wiring (out of R15 scope).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.common import SuccessEnvelope, Meta
from app.services.official_source import list_official_sources

official_router = APIRouter(prefix="/official-sources", tags=["official-sources"])


@official_router.get("")
def list_official() -> dict:
    items = list_official_sources()
    return SuccessEnvelope(
        data={
            "sources": items,
            "connected_count": sum(1 for s in items if s.get("connected")),
            "state": "not_connected" if not items else "partial",
        },
        meta=Meta(),
    ).model_dump()
