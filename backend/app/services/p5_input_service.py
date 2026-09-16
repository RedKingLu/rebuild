"""P5 输入事实源服务（R12-3-C1）。

权威路径：**DB artifact refs + 项目工作区文件路径**（D-105②）。
不新增顶层 ``<repo>/output_code/`` 或 ``patches/`` 第二事实源。

职责：
  给定 project_id / run_id，为 RealP5Handler（C3 起）提供对 P4 产物的稳定读取：
    - output_code refs（来自 P4 worker 写入 + task_node_run.artifact_refs + p4_execution_summary.change_manifest）
    - patch refs（同上 + p4_execution_summary.patch_index）
    - evidence refs（AETService 真实 Evidence 对象列表 + p4_execution_summary.evidence_refs）
    - P4 execution summary（artifacts/p4/p4_execution_summary.json 解析，D-107 分层）
    - P4→P5 Gate 状态（p_gate 表 gate_type=stage_promotion stage=p4）

诚实状态（V10 教训 / D-066 / D-101）：
  - refs 缺失 / 文件消失 → evidence_gap（不伪造、不跳过）
  - Gate 未 approved → blocked
  - source/ 绝不作为新代码交付主体（D-099① / D-105③）

本环节只新增服务 + API 路由 + 单测。**不注册 RealP5Handler**（C3）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.database import get_session
from app.services.workspace_service import workspace_path

logger = logging.getLogger(__name__)


# ── DTOs ──────────────────────────────────────────────────────────────────

@dataclass
class P4InputFacts:
    """P5 对 P4 产物的读取结果。单一事实源 + 诚实状态。"""
    project_id: str
    run_id: str
    # refs（相对项目工作区的路径列表）
    output_code_refs: list[str] = field(default_factory=list)
    patch_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    # summary
    p4_execution_summary: Optional[dict] = None
    p4_summary_ref: Optional[str] = None
    # gate
    p4_to_p5_gate_id: Optional[str] = None
    p4_to_p5_gate_status: Optional[str] = None  # approved / waiting_decision / None
    # honesty
    evidence_gaps: list[dict] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: Optional[str] = None
    # which path was used (for traceability)
    run_id_used: Optional[str] = None
    node_run_count: int = 0


# ── Service ───────────────────────────────────────────────────────────────

class P5InputService:
    """P5 输入事实源服务。R12-3-C1 新建。

    C1 完成标准：
      - P5 能通过 project_id/run_id 读取 P4 output_code refs、patch refs、evidence refs、summary。
      - refs 缺失、文件缺失、Gate 未 approved 均诚实 blocked/evidence_gap。
      - source 不被读取为新代码交付主体。
    """

    def __init__(self, services=None):
        self._svc = services

    def _services(self):
        if self._svc is None:
            from app.dependencies import get_services
            self._svc = get_services()
        return self._svc

    # ── public API ───────────────────────────────────────────────────────

    def read_p4_input(self, project_id: str, run_id: str) -> P4InputFacts:
        """Build the complete P4-input fact bundle for P5 verification.

        Follows D-105: DB artifact refs + workspace file paths (no second truth source).
        """
        facts = P4InputFacts(project_id=project_id, run_id=run_id)
        facts.run_id_used = run_id

        # 1. Verify the P4→P5 Gate is approved (D-023/D-092)
        gate_ok, gate_reason = self._check_p4_gate(project_id, run_id, facts)
        if not gate_ok:
            facts.blocked = True
            facts.blocked_reason = gate_reason
            return facts

        # 2. Gather artifact_refs from task_node_run rows (DB is the single truth source)
        self._gather_run_refs(project_id, run_id, facts)

        # 3. Read P4 execution summary from artifacts/p4/p4_execution_summary.json
        self._read_p4_summary(project_id, facts)

        # 4. Derive evidence refs (from summary + AETService cross-check)
        self._gather_evidence_refs(project_id, facts)

        # 5. Validate: every ref must resolve to an existing file; missing → evidence_gap
        self._validate_refs(project_id, facts)

        return facts

    # ── Gate ─────────────────────────────────────────────────────────────

    def _check_p4_gate(self, project_id: str, run_id: str, facts: P4InputFacts) -> tuple[bool, str]:
        """P4→P5 晋级 Gate 必须是 approved；否则 blocked（诚实，不绕过）。"""
        try:
            from app.models.gate import Gate
            db = get_session()
            try:
                g = (db.query(Gate)
                     .filter(Gate.project_id == project_id,
                             Gate.run_id == run_id,
                             Gate.gate_type == "stage_promotion",
                             Gate.stage == "p4")
                     .order_by(Gate.gate_id.desc())
                     .first())
                if g is None:
                    facts.evidence_gaps.append({
                        "gap_id": "no_p4_gate",
                        "evidence_type": "gate_exists",
                        "description": f"未找到 P4→P5 Gate（project_id={project_id}）",
                        "blocking": True,
                    })
                    return False, "P4→P5 Gate 不存在（P4 未完成或未创建晋级 Gate）"
                facts.p4_to_p5_gate_id = g.gate_id
                facts.p4_to_p5_gate_status = g.gate_status
                if g.gate_status != "approved":
                    facts.evidence_gaps.append({
                        "gap_id": "p4_gate_not_approved",
                        "evidence_type": "gate_approved",
                        "description": f"P4→P5 Gate {g.gate_id} 状态为 {g.gate_status}（非 approved）",
                        "blocking": True,
                    })
                    return False, f"P4→P5 Gate 未通过（{gate_status_label(g.gate_status)}）"
                return True, ""
            finally:
                db.close()
        except Exception as e:
            logger.warning("P5 input: gate check failed (honest blocked): %s", e, exc_info=True)
            return False, f"Gate 检查异常（诚实中断）：{type(e).__name__}"

    # ── Refs from DB ────────────────────────────────────────────────────

    def _gather_run_refs(self, project_id: str, run_id: str, facts: P4InputFacts):
        """从 task_node_run 表读取 artifact_refs / evidence_refs。

        DB 是单一事实源；refs 中的路径是相对于项目工作区的。
        """
        try:
            from app.models.task_node_run import TaskNodeRun
            db = get_session()
            try:
                rows = (db.query(TaskNodeRun)
                        .filter(TaskNodeRun.project_id == project_id,
                                TaskNodeRun.run_id == run_id)
                        .all())
                facts.node_run_count = len(rows)
                art_set: dict[str, None] = {}
                ev_set: dict[str, None] = {}
                for r in rows:
                    for ref in (r.artifact_refs or []):
                        if isinstance(ref, str):
                            art_set[ref] = None
                    for ref in (r.evidence_refs or []):
                        if isinstance(ref, str):
                            ev_set[ref] = None
                facts.output_code_refs = sorted(r for r in art_set if r.startswith("output_code/"))
                facts.patch_refs = sorted(r for r in art_set if r.startswith("patches/"))
                # evidence_refs are stored as evidence_id in task_node_run; we cross-check below
                facts.evidence_refs = sorted(ev_set)
            finally:
                db.close()
        except Exception as e:
            logger.warning("P5 input: run-ref gather failed: %s", e, exc_info=True)
            facts.evidence_gaps.append({
                "gap_id": "run_refs_unreadable",
                "evidence_type": "artifact_refs",
                "description": f"task_node_run 读取异常：{type(e).__name__}",
                "blocking": False,
            })

    # ── P4 summary ────────────────────────────────────────────────────────

    def _read_p4_summary(self, project_id: str, facts: P4InputFacts):
        """Read artifacts/p4/p4_execution_summary.json（真实落盘产物，D-107 分层）。

        R17.5 P4/T4.1：报告迁至 artifacts/p4/；兼容读取旧的扁平 artifacts/p4_execution_summary.json
        （历史工作区），以新路径优先。"""
        ws = workspace_path(project_id)
        new_rel = "artifacts/p4/p4_execution_summary.json"
        legacy_rel = "artifacts/p4_execution_summary.json"
        summary_path = ws / new_rel
        summary_rel = new_rel
        if not summary_path.exists() and (ws / legacy_rel).exists():
            summary_path = ws / legacy_rel
            summary_rel = legacy_rel
        if not summary_path.exists():
            facts.evidence_gaps.append({
                "gap_id": "no_p4_summary",
                "evidence_type": "p4_execution_summary",
                "description": "artifacts/p4/p4_execution_summary.json 不存在",
                "blocking": False,
            })
            return
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            facts.p4_execution_summary = data
            facts.p4_summary_ref = summary_rel
            # Merge change_manifest / patch_index refs if not already present
            cm = data.get("change_manifest", [])
            pi = data.get("patch_index", [])
            art_set = set(facts.output_code_refs) | set(facts.patch_refs)
            for item in cm:
                p = item.get("path", "")
                if p.startswith("output_code/"):
                    art_set.add(p)
            for item in pi:
                p = item.get("path", "")
                if p.startswith("patches/"):
                    art_set.add(p)
            facts.output_code_refs = sorted(r for r in art_set if r.startswith("output_code/"))
            facts.patch_refs = sorted(r for r in art_set if r.startswith("patches/"))
            # evidence_refs from summary
            for eid in data.get("evidence_refs", []):
                if eid not in facts.evidence_refs:
                    facts.evidence_refs.append(eid)
            facts.evidence_refs.sort()
        except Exception as e:
            logger.warning("P5 input: summary parse failed: %s", e, exc_info=True)
            facts.evidence_gaps.append({
                "gap_id": "p4_summary_unparseable",
                "evidence_type": "p4_execution_summary",
                "description": f"p4_execution_summary.json 解析异常：{type(e).__name__}",
                "blocking": False,
            })

    # ── Evidence refs ─────────────────────────────────────────────────────

    def _gather_evidence_refs(self, project_id: str, facts: P4InputFacts):
        """Cross-check evidence refs via AETService（真实 Evidence 对象）。"""
        try:
            svc = self._services()
            aet = svc.aet_service
            existing = aet.list_evidence(project_id, stage="p4")
            aet_ids = {e.get("evidence_id") for e in existing if e.get("evidence_id")}
            # Any id from DB refs that is MISSING from disk → evidence_gap
            for eid in list(facts.evidence_refs):
                if eid not in aet_ids:
                    facts.evidence_gaps.append({
                        "gap_id": f"evidence_missing_{eid}",
                        "evidence_type": "evidence_exists",
                        "description": f"Evidence {eid} 在 task_node_run 中引用但 evidence/ 目录中不存在",
                        "blocking": False,
                    })
        except Exception as e:
            logger.warning("P5 input: evidence gather failed: %s", e, exc_info=True)

    # ── Validation ────────────────────────────────────────────────────────

    def _validate_refs(self, project_id: str, facts: P4InputFacts):
        """Every output_code / patch ref must resolve to an existing file."""
        ws = workspace_path(project_id)
        for ref in facts.output_code_refs:
            if not (ws / ref).exists():
                facts.evidence_gaps.append({
                    "gap_id": f"output_code_missing_{ref}",
                    "evidence_type": "output_code_exists",
                    "description": f"output_code ref {ref} 在 DB/summary 中引用但文件不存在",
                    "blocking": False,
                })
        for ref in facts.patch_refs:
            if not (ws / ref).exists():
                facts.evidence_gaps.append({
                    "gap_id": f"patch_missing_{ref}",
                    "evidence_type": "patch_exists",
                    "description": f"patch ref {ref} 在 DB/summary 中引用但文件不存在",
                    "blocking": False,
                })


def gate_status_label(status: str) -> str:
    return {"approved": "已通过", "waiting_decision": "等待决策",
            "rejected": "已拒绝", "changes_requested": "已要求修改"}.get(status, status)


def p4_input_facts_to_dict(facts: P4InputFacts) -> dict:
    """Serialize for API response."""
    return {
        "project_id": facts.project_id,
        "run_id": facts.run_id,
        "run_id_used": facts.run_id_used,
        "node_run_count": facts.node_run_count,
        "output_code_refs": facts.output_code_refs,
        "patch_refs": facts.patch_refs,
        "evidence_refs": facts.evidence_refs,
        "p4_summary_ref": facts.p4_summary_ref,
        "p4_execution_summary": facts.p4_execution_summary,
        "p4_to_p5_gate_id": facts.p4_to_p5_gate_id,
        "p4_to_p5_gate_status": facts.p4_to_p5_gate_status,
        "evidence_gaps": facts.evidence_gaps,
        "blocked": facts.blocked,
        "blocked_reason": facts.blocked_reason,
    }
