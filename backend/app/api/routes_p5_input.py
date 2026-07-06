"""P5 输入事实源 API 路由（R12-3-C1）。

为 RealP5Handler（C3 起）和 StagePageP5（C7 起）提供 P4 产物读取端点。
权威路径：DB artifact refs + 项目工作区文件路径（D-105②）。

端点：
  GET /api/projects/{project_id}/runs/{run_id}/p5/input
    → P4InputFacts 完整包（refs + summary + gate + evidence_gaps）
    → Gate 未 approved → 423 Locked（诚实 blocked，不绕过）
    → refs 缺失 → 200 + evidence_gaps 列表（诚实标记，不伪造）

  GET /api/projects/{project_id}/runs/{run_id}/p5/input/summary
    → 仅 P4 execution summary（artifacts/p4_execution_summary.json 解析）
    → 文件不存在 → 404（诚实）

  GET /api/projects/{project_id}/runs/{run_id}/p5/input/gate
    → P4→P5 Gate 状态（gate_id / gate_status / approved?）
    → Gate 不存在 → 404
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from app.schemas.common import SuccessEnvelope, Meta
from app.services.p5_input_service import (
    P5InputService, p4_input_facts_to_dict,
)

logger = logging.getLogger("rebuild.routes_p5_input")

router = APIRouter(prefix="/projects/{project_id}/runs/{run_id}/p5", tags=["p5-input"])


def _svc() -> P5InputService:
    return P5InputService()


@router.get("/input")
async def get_p5_input(project_id: str, run_id: str):
    """P5 完整输入事实源包。

    - Gate 未 approved → 423（诚实 blocked）
    - refs 缺失 → 200 + evidence_gaps（诚实标记）
    - 无 P4 产物 → 200 + blocked=True + blocked_reason
    """
    svc = _svc()
    facts = svc.read_p4_input(project_id, run_id)

    if facts.blocked:
        # D-023/D-092: P4→P5 Gate 未通过 → 诚实 423，不绕过
        raise HTTPException(
            status_code=423,
            detail={
                "message": "P5 输入未就绪（P4→P5 Gate 未通过或 P4 未完成）",
                "blocked_reason": facts.blocked_reason,
                "p4_to_p5_gate_id": facts.p4_to_p5_gate_id,
                "p4_to_p5_gate_status": facts.p4_to_p5_gate_status,
                "evidence_gaps": facts.evidence_gaps,
            },
        )

    # R12-18 修复 R12-4-04：若 P5 handler 已执行并持久化验证报告，合并返回报告
    data = p4_input_facts_to_dict(facts)
    validation_report = _load_p5_validation_report(project_id)
    if validation_report is not None:
        data["p5_validation_report"] = validation_report

    return SuccessEnvelope(data=data, meta=Meta())


def _load_p5_validation_report(project_id: str) -> dict | None:
    """读取 P5 handler 执行后持久化的验证报告（R12-4-04）。"""
    try:
        from app.services.workspace_service import workspace_path
        from app.services.workspace_mediator import WorkspaceMediator
        report_path = workspace_path(project_id) / "artifacts" / "p5_validation_report.json"
        if not report_path.exists():
            return None
        WorkspaceMediator(str(workspace_path(project_id))).guard_read(str(report_path))
        return json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("P5 input: load validation report failed: %s", e, exc_info=True)
        return None


@router.get("/input/summary")
async def get_p5_input_summary(project_id: str, run_id: str):
    """仅 P4 execution summary（artifacts/p4_execution_summary.json）。"""
    svc = _svc()
    facts = svc.read_p4_input(project_id, run_id)
    if facts.p4_execution_summary is None:
        raise HTTPException(404, "P4 execution summary 不存在（artifacts/p4_execution_summary.json）")
    return SuccessEnvelope(data={
        "project_id": project_id,
        "run_id": run_id,
        "p4_summary_ref": facts.p4_summary_ref,
        "p4_execution_summary": facts.p4_execution_summary,
    }, meta=Meta())


@router.get("/input/gate")
async def get_p5_input_gate(project_id: str, run_id: str):
    """P4→P5 Gate 状态。"""
    svc = _svc()
    facts = svc.read_p4_input(project_id, run_id)
    if facts.p4_to_p5_gate_id is None:
        raise HTTPException(404, "P4→P5 Gate 不存在")
    return SuccessEnvelope(data={
        "project_id": project_id,
        "run_id": run_id,
        "p4_to_p5_gate_id": facts.p4_to_p5_gate_id,
        "p4_to_p5_gate_status": facts.p4_to_p5_gate_status,
        "approved": facts.p4_to_p5_gate_status == "approved",
    }, meta=Meta())
