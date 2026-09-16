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


# B-ACC-HELD-ACTION-INVISIBLE：挂起动作采集**只有一份实现**（services/held_actions），
# 本模块与 validation_agent 都 import 它。此处做成模块级薄封装是为了让采集失败不至于
# 让整份 Gate Brief 写不出来（该报告是用户 Gate 决策的主材料，不可因附加段落而丢失）。
def _collect_held(project_id: str, run_id: str, stage: str) -> List[dict]:
    try:
        from app.services.held_actions import collect_held_actions
        return collect_held_actions(project_id, run_id, stage)
    except Exception:
        import logging
        logging.getLogger("rebuild.stage_reports").warning(
            "held_actions 采集不可用 project=%s stage=%s —— held_actions 段为空"
            "【不代表无挂起动作】", project_id, stage, exc_info=True)
        return []


def _merge_held_note(honest_notes: Optional[str], held: List[dict]) -> str:
    try:
        from app.services.held_actions import merge_held_actions_note
        return merge_held_actions_note(honest_notes, held)
    except Exception:
        # 发声（公理 3）：走到这里意味着已采集到的挂起动作**没能并入 honest_notes**，
        # 即产物在这一处又变回"看不出有动作被挂起"——正是本条台账要治的形态，不可静默。
        # 上面 `_collect_held` 的失败已单独发声，两处失败原因不同故不合并。
        import logging
        logging.getLogger("rebuild.stage_reports").warning(
            "挂起动作说明并入 honest_notes 失败（已采集到 %d 条挂起动作，"
            "本份产物的 honest_notes 不含它们；held_actions 段仍有原始数据）",
            len(held or []), exc_info=True)
        return (honest_notes or "").strip()


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
                   claim_evidence_summary: Optional[dict] = None,
                   run_id: str = "") -> str:
        """用户可读阶段审核摘要（内容真实，D-101）。WorkAgent 侧 + ValidationAgent 侧合成。

        B-ACC-HELD-ACTION-INVISIBLE 解除条件 ①②：本报告须反映"本阶段有 N 个动作因待审批
        未执行"并列出 gate_id，且该信息须并入 `honest_notes` —— 否则用户在 Gate 上读到的
        是一份"什么都完成了"的摘要，而实际有 L4 动作被 fail-closed 拦下从未执行。
        采集实现只有一份（`services/held_actions`），与 validation 报告共用。
        `run_id` 可选：缺省（空串）时按 project+stage 采集，不因调用方没传 run_id 就跳过采集。
        """
        held = _collect_held(self.project_id, run_id, stage)
        return self._write_json("gate_brief", {
            "what_happened": what_happened,
            "key_artifacts": key_artifacts,
            "risks": risks,
            "validation_verdict": validation_verdict,
            "claim_evidence_summary": claim_evidence_summary or {},
            "decision_options": ["approve", "reject", "request_changes"],
            "held_actions": held,
            "held_action_count": len(held),
            "honest_notes": _merge_held_note(honest_notes, held),
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
