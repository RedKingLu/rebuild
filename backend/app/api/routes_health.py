"""Health, version, and meta endpoints.

R4: Real endpoints — always return actual platform metadata.
"""

from fastapi import APIRouter

from app.core.capability import CAPABILITY_STATUSES
from app.core.status import (
    EXECUTION_MODES,
    RISK_LEVELS,
    R_STAGES,
    P_STAGES,
)

router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    """Platform health check."""
    return {
        "status": "ok",
        "platform": "rebuild",
        "version": "V26.1.1",
        "r_stage": "R4",
    }


@router.get("/version")
async def version():
    """Platform version information."""
    return {
        "platform": "rebuild",
        "version": "V26.1.1",
        "r_stage": "R4",
        "stage_name": "基础工程骨架与 API 契约落地",
    }


@router.get("/meta")
async def meta():
    """Platform enumerations and capability metadata."""
    return {
        "enums": {
            "r_stages": R_STAGES,
            "p_stages": P_STAGES,
            "execution_modes": EXECUTION_MODES,
            "risk_levels": RISK_LEVELS,
            "capability_statuses": CAPABILITY_STATUSES,
        },
        "graph_status": {
            "graph_capability_status": "not_connected",
            "transition_mode": "mock",
        },
        "source_status": "mock",
    }
