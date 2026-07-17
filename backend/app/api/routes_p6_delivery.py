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

    # SEC-01（WP-4）：脱敏硬门禁。含疑似密钥/凭据时默认硬阻断交付；唯一放行路径 = 用户
    # 显式批准 desensitization_release Gate。未放行 → 422 + 强制 Gate + 脱敏后风险说明。
    if not pkg.desensitization_ok:
        from app.services.p6_delivery_service import (
            find_approved_desensitization_override, ensure_desensitization_gate,
            desensitization_risk_explanation,
        )
        override = find_approved_desensitization_override(project_id, run_id)
        if not override:
            from app.dependencies import get_services
            _svc2 = get_services()
            gate_id = ensure_desensitization_gate(
                project_id, run_id, pkg,
                tracer=_svc2.trace_writer, auditor=_svc2.audit_writer)
            raise HTTPException(422, {
                "error": "SEC-01 脱敏硬门禁：交付包含疑似密钥/凭据，默认阻断交付",
                "desensitization_gate_id": gate_id,
                "risk_explanation": desensitization_risk_explanation(pkg),
            })

    data = delivery_package_to_dict(pkg)
    data["desensitization_released"] = (not pkg.desensitization_ok)
    return SuccessEnvelope(data=data, meta=Meta())


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

    # SEC-01（WP-4）：单文件下载脱敏硬门禁。若该文件含疑似密钥且用户未显式批准脱敏放行
    # Gate，则拒绝下载（防止绕过 package 门禁直接下载泄密文件）。
    import re as _re
    _secret_pats = [
        _re.compile(r"sk-[a-z0-9]{20,}", _re.IGNORECASE),
        _re.compile(r"AKIA[0-9A-Z]{16}"),
        _re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']?([^\s\"']{8,})"),
    ]
    if any(p.search(content) for p in _secret_pats):
        from app.services.p6_delivery_service import find_approved_desensitization_override
        if not find_approved_desensitization_override(project_id, run_id):
            raise HTTPException(403, "SEC-01 脱敏硬门禁：该文件含疑似密钥/凭据，未经用户显式放行不得下载")

    return PlainTextResponse(content=content, media_type="text/plain",
                              headers={"Content-Disposition": f"attachment; filename={target.name}"})
