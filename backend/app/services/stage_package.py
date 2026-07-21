"""Stage Completion Package (D-107) + per-stage artifact folder helpers.

D-107（2026-07-21，accepted）落地术语表「Stage Completion Package 阶段完成包」/
「Stage Artifact Slot 阶段产物槽位」：

  1. 每个 P 阶段的领域产物写入 ``artifacts/{stage}/``（p0/p1/…/p6），保留
     ``artifacts/`` 根（DOC-2 单一事实源约定更新为 ``artifacts/{stage}/*.json``）。
     仍是工作区文件路径、仅分层嵌套——不违 D-105②（无新增顶层第二事实源）。
  2. 每阶段末产出 ``artifacts/{stage}/_stage_package.json`` 作为阶段完成包清单：
     ``products[{file,type,desc,key_for_next}]`` + 证据/风险摘要 + 进入下一阶段建议。
  3. 下一阶段读**前序各阶段** package 清单（描述），据此**按需加载**所需产物内容
     （清单驱动，非写死文件名列表；对齐 AGENTS §2.3 —— Agent 决定读什么）。

本模块只提供路径/读写工具，不含任何识别/评估逻辑（确定性采集层）。写入统一经
WorkspaceMediator（D-099⑥ 单一写门 + source/ 拒写）。R17.5 P2 轮统一 P0/P1/P2 落地，
P3-P6 遵循。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.workspace_service import workspace_path

logger = logging.getLogger("rebuild.stage_package")

# 阶段完成包清单文件名（下划线前缀：与领域产物区分，不计入 products）。
STAGE_PACKAGE_FILENAME = "_stage_package.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stage_artifact_ref(stage: str, filename: str) -> str:
    """本阶段某产物的工作区相对 ref（D-107 分层路径）：``artifacts/{stage}/{filename}``。"""
    return f"artifacts/{stage}/{filename}"


def stage_artifact_dir(project_id: str, stage: str) -> Path:
    """本阶段产物目录：``<workspace>/artifacts/{stage}``。"""
    return workspace_path(project_id) / "artifacts" / stage


def _mediated_write(project_id: str, rel_path: str, content: str, *,
                    auditor=None, stage: str, action: str) -> str:
    """经 WorkspaceMediator 写工作区文件（单一写门 D-099⑥；source/ 拒写、逃逸抛错）。

    与 stage_handlers._mediated_write 同语义；此处独立实现以避免循环导入
    （stage_handlers 依赖本模块）。返回 rel_path。
    """
    from app.services.workspace_mediator import WorkspaceMediator
    mediator = WorkspaceMediator(str(workspace_path(project_id)))
    target, risk = mediator.check_write(rel_path)  # raises ValueError on source/ or escape
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if auditor:
        try:
            auditor.write("workspace_write", risk_level=risk, action=action,
                          decision="allowed", reason=f"wrote {rel_path}",
                          project_id=project_id, stage=stage, transition_mode="real")
        except Exception:
            logger.warning("stage package audit write failed (advisory)", exc_info=True)
    return rel_path


def write_stage_package(project_id: str, stage: str, *,
                        products: list,
                        evidence_summary: Optional[dict] = None,
                        risks: Optional[list] = None,
                        next_stage_advice: str = "",
                        auditor=None,
                        tracer=None,
                        extra: Optional[dict] = None) -> str:
    """写 ``artifacts/{stage}/_stage_package.json`` 阶段完成包清单，返回其相对 ref。

    products：每项 ``{"file","type","desc","key_for_next"}``。``key_for_next=True`` 标记
    下阶段关键产物——下游据此按需加载内容（无需硬编码文件名）。
    """
    package = {
        "stage": stage,
        "completed_at": _now(),
        "products": products or [],
        "evidence_summary": evidence_summary or {},
        "risks": risks or [],
        "next_stage_advice": next_stage_advice or "",
    }
    if extra:
        package.update(extra)
    rel = stage_artifact_ref(stage, STAGE_PACKAGE_FILENAME)
    _mediated_write(project_id, rel, json.dumps(package, ensure_ascii=False, indent=2),
                    auditor=auditor, stage=stage, action="write_stage_package")
    if tracer:
        try:
            tracer.write("stage_package", action="write_stage_package",
                         summary=f"{stage} 阶段完成包：{len(package['products'])} 产物",
                         project_id=project_id, stage=stage)
        except Exception:
            logger.debug("stage package trace 写入失败（advisory）", exc_info=True)
    return rel


def read_stage_package(project_id: str, stage: str) -> Optional[dict]:
    """读某阶段的完成包清单（``artifacts/{stage}/_stage_package.json``）。不存在→None。"""
    fp = stage_artifact_dir(project_id, stage) / STAGE_PACKAGE_FILENAME
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        # 发声：清单存在但损坏，静默会让下游误判"上阶段无产物"，须可见（公理3）。
        logger.warning("read_stage_package: %s/%s 解析失败 project=%s",
                       stage, STAGE_PACKAGE_FILENAME, project_id, exc_info=True)
        return None


def product_entry(file: str, type: str, desc: str, key_for_next: bool = False) -> dict:
    """构造一条 products 清单项（供各阶段 handler 复用，字段固定通用）。"""
    return {"file": file, "type": type, "desc": desc, "key_for_next": bool(key_for_next)}
