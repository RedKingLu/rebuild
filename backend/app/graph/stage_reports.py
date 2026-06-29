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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StageReports:
    """Produce/locate the three review reports for a stage in a project workspace."""

    KINDS = ("start_plan", "construction", "acceptance")

    def __init__(self, project_id: str, stage: str):
        self.project_id = project_id
        self.stage = stage
        self.artifacts_dir: Path = workspace_service.workspace_path(project_id) / "artifacts"
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
    def acceptance(self, *, passed: bool, issues: List[str],
                   recommendations: List[str], reviewer: str = "review_pass") -> str:
        return self._write_json("acceptance", {
            "passed": passed,
            "issues": issues,
            "recommendations": recommendations,
            "reviewer": reviewer,
        })

    def all_refs(self) -> List[str]:
        """Return refs for the three reports that currently exist on disk."""
        refs: List[str] = []
        root = workspace_service.workspace_path(self.project_id)
        for kind in self.KINDS:
            p = self._path(kind)
            if p.exists():
                refs.append(str(p.relative_to(root)))
        return refs
