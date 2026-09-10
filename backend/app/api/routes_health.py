"""Health, version, and meta endpoints.

R4: Real endpoints — always return actual platform metadata.
R17-2 V-R17-1B-1/P1-4：r_stage 改为读配置事实源，不硬编码过期 R10。
R22 批次六（R22-07）：version 改为读 settings.app_version（推翻 R19-3-05
  「health 版本声明按验收口径不动」的取舍，理由详见 core/config.py 的 app_version 注释）；
  /api/version 的 stage_name 字段删除——它是写死的中文阶段描述，已过期停在 R12，
  且全仓（含 frontend/src、文档/、backend/）grep 确认无任何消费方读该字段
  （同名 stage_name 命中的是 P 阶段实体字段 schemas/stage.py，与本响应无关）。
  阶段事实由 r_stage 承载，已有配置单一事实源，不再另设一个必然过期的描述字段。
"""

from fastapi import APIRouter

from app.core.config import settings
from app.core.capability import CAPABILITY_STATUSES
from app.core.status import (
    EXECUTION_MODES,
    RISK_LEVELS,
    R_STAGES,
    P_STAGES,
)

router = APIRouter(tags=["system"])


def _current_r_stage() -> str:
    """单一事实源：backend/.env 中 REBUILD_R_STAGE（或在 config.py 设默认）。
    未知则返 unknown（诚实声明，不伪造 R10）。
    """
    stage = getattr(settings, "r_stage", None)
    return stage if stage else "unknown"


@router.get("/health")
async def health():
    """Platform health check."""
    return {
        "status": "ok",
        "platform": "rebuild",
        "version": settings.app_version,
        "r_stage": _current_r_stage(),
    }


@router.get("/version")
async def version():
    """Platform version information."""
    return {
        "platform": "rebuild",
        "version": settings.app_version,
        "r_stage": _current_r_stage(),
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
            "graph_capability_status": _graph_capability(),
            "transition_mode": "langgraph",
        },
        "source_status": "real",
    }


def _graph_capability() -> str:
    try:
        from app.graph.runtime import graph_capability_probe
        return graph_capability_probe()
    except Exception:
        return "degraded"
