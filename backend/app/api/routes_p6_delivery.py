"""P6 交付包 API 路由（R12-3-C10）。

端点：
  GET /api/projects/{project_id}/runs/{run_id}/p6/package
    → 完整 DeliveryPackage JSON（交付包目录树 + 报告 + 索引 + 清单）
    → P6 handler 未执行 → 404（诚实）

  GET /api/projects/{project_id}/runs/{run_id}/p6/download?path=...
    → 下载交付包中的单个文件（经 workspace _guard 校验）
    → source/ 无条件拒绝
    → 仅允许 output_code/ 和 patches/ 下的文件
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.schemas.common import SuccessEnvelope, Meta
from app.services.workspace_service import workspace_path
from app.services.p6_delivery_service import P6DeliveryService, delivery_package_to_dict
from app.graph.stage_handlers import RealP6Handler

logger = logging.getLogger("rebuild.routes_p6_delivery")

router = APIRouter(prefix="/projects/{project_id}/runs/{run_id}/p6", tags=["p6-delivery"])


def _svc() -> P6DeliveryService:
    return P6DeliveryService()


@router.get("/package")
async def get_p6_package(project_id: str, run_id: str):
    """P6 交付包完整信息（由前端 StagePageP6 渲染）。"""
    # R12-7 修复 B-P6-UNGATED-BY-P5：P5 未通过 → 拒绝交付
    if not RealP6Handler._check_p5_validation_passed(project_id, run_id):
        raise HTTPException(422, "P5 验证未通过，P6 交付包不得生成")

    svc = _svc()
    try:
        pkg = svc.generate_delivery_package(project_id, run_id)
    except Exception as e:
        raise HTTPException(500, f"交付包生成异常：{type(e).__name__}")

    if pkg.risk_manifest.get("blocking"):
        raise HTTPException(422, pkg.risk_manifest.get("error", "交付包生成阻断"))

    return SuccessEnvelope(data=delivery_package_to_dict(pkg), meta=Meta())


@router.get("/download")
async def download_p6_file(project_id: str, run_id: str, path: str = ""):
    """下载交付包中的单个文件（仅 output_code/ 和 patches/）。"""
    from app.services.workspace_mediator import WorkspaceMediator

    if not path:
        raise HTTPException(400, "path 参数缺失")

    ws = workspace_path(project_id)
    try:
        target = WorkspaceMediator(str(ws)).guard_read(path)
    except ValueError as e:
        raise HTTPException(403, str(e))

    # 仅允许 output_code/ 和 patches/
    top = path.split("/")[0]
    if top not in ("output_code", "patches"):
        raise HTTPException(403, f"仅允许下载 output_code/ 和 patches/ 下的文件（非 {top}/）")

    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{path}")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(500, f"读取失败：{e}")

    return PlainTextResponse(content=content, media_type="text/plain",
                              headers={"Content-Disposition": f"attachment; filename={target.name}"})
