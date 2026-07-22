"""StageReports — three-report framework for every enabled P-stage (R9-5-1, T8 / D-092).

Each enabled stage produces three user-reviewable reports, attached as Gate review
material (D-083):
  ① 起始计划报告 (start_plan)      — what this stage will do + acceptance criteria
  ② 中间施工报告 (construction)    — execution log / key actions, incl. each rework round
  ③ 验收报告     (acceptance)      — Acceptance/Review verdict: passed / issues / recommendations

Reports land under the project workspace `artifacts/` as real files and their paths
are returned as artifact_refs for GateService.create(artifact_refs=...).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.services import workspace_service
from app.services.stage_package import stage_artifact_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StageReports:
    """Produce/locate the three review reports for a stage in a project workspace.

    D-107: reports land under ``artifacts/{stage}/`` (per-stage subdirectory),
    NOT the flat ``artifacts/`` root.  This aligns with intake_service /
    source_materializer / stage_package which all use ``stage_artifact_dir()``.
    """

    KINDS = ("start_plan", "construction", "acceptance")

    def __init__(self, project_id: str, stage: str):
        self.project_id = project_id
        self.stage = stage
        # D-107 fix: write into the per-stage subdirectory, not the flat root.
        self.artifacts_dir: Path = stage_artifact_dir(project_id, stage)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, ext: str = "json") -> Path:
        return self.artifacts_dir / f"{self.stage}_{kind}.{ext}"

    def _write_json(self, kind: str, payload: dict) -> str:
        p = self._path(kind)
        payload = {"stage": self.stage, "kind": kind, "generated_at": _now(), **payload}
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p.relative_to(workspace_service.workspace_path(self.project_id)))

    # ── ① 起始计划报告 ──────────────────────────────────────────────
    def start_plan(self, *, goal: str, acceptance_criteria: List[str],
                   planned_actions: List[str]) -> str:
        return self._write_json("start_plan", {
            "goal": goal,
            "acceptance_criteria": acceptance_criteria,
            "planned_actions": planned_actions,
        })

    # ── ② 中间施工报告 ──────────────────────────────────────────────
    def construction(self, *, rounds: List[dict], actions: List[str],
                     produced_artifacts: List[str]) -> str:
        return self._write_json("construction", {
            "rounds": rounds,            # each: {round, status, issues}
            "actions": actions,
            "produced_artifacts": produced_artifacts,
        })

    # ── ③ 验收报告 ──────────────────────────────────────────────────
    def acceptance(self, *, passed: bool, issues: List[dict],
                   recommendations: List[str], reviewer: str = "review_pass") -> str:
        # FUP-3 (R17.3-6 WP-5): issues 为结构化对象列表（每项 {type/detail} 或
        # {self_check}），非字符串化 dict。前端 GatePanel 按对象渲染（x.detail/x.type）。
        return self._write_json("acceptance", {
            "passed": passed,
            "issues": issues,
            "recommendations": recommendations,
            "reviewer": reviewer,
        })

    # ── ④ 动态工作计划报告（R17.3-6 WP-2，AGT-03） ─────────────────────
    def work_plan(self, *, generated_by: str, based_on: dict, goal: str,
                  planned_actions: List[dict], acceptance_criteria: List[str],
                  skill_ref: Optional[dict] = None,
                  risks_foreseen: Optional[List[str]] = None) -> str:
        """WorkAgent 基于真实项目事实确定性合成的动态工作计划（非 handler 静态模板）。"""
        return self._write_json("work_plan", {
            "generated_by": generated_by,
            "based_on": based_on,
            "goal": goal,
            "planned_actions": planned_actions,
            "acceptance_criteria": acceptance_criteria,
            "skill_ref": skill_ref,
            "risks_foreseen": risks_foreseen or [],
        })

    # ── ⑤ Gate Brief 报告（R17.3-6 WP-2，AGT-02） ──────────────────────
    def gate_brief(self, *, stage: str, what_happened: str, key_artifacts: List[dict],
                   risks: List[dict], honest_notes: str = "",
                   validation_verdict: Optional[dict] = None,
                   claim_evidence_summary: Optional[dict] = None) -> str:
        """用户可读阶段审核摘要（内容真实，D-101）。WorkAgent 侧 + ValidationAgent 侧合成。"""
        return self._write_json("gate_brief", {
            "what_happened": what_happened,
            "key_artifacts": key_artifacts,
            "risks": risks,
            "validation_verdict": validation_verdict,
            "claim_evidence_summary": claim_evidence_summary or {},
            "decision_options": ["approve", "reject", "request_changes"],
            "honest_notes": honest_notes,
        })

    # ── ⑤b fact/claim-evidence map 报告（R17.3-6 WP-2，AGT-05/EVI-01） ───
    def claim_evidence_map(self, *, map_type: str, entries: List[dict]) -> str:
        """每条 claim/fact 绑定 artifact/evidence/trace/audit 引用。"""
        return self._write_json("claim_evidence_map", {
            "map_type": map_type,   # claim_evidence | fact_evidence
            "entries": entries,
        })

    # ── ⑥ 独立 ValidationAgent 验收结论报告（R17.3-6 WP-2，D-082） ───────
    def validation(self, payload: dict) -> str:
        """独立 ValidationAgent 验收结论（区别于 StageLoop 的 review_pass acceptance 报告）。"""
        return self._write_json("validation", payload)

    def all_refs(self) -> List[str]:
        """Return refs for the three reports that currently exist on disk."""
        refs: List[str] = []
        root = workspace_service.workspace_path(self.project_id)
        for kind in self.KINDS:
            p = self._path(kind)
            if p.exists():
                refs.append(str(p.relative_to(root)))
        return refs
