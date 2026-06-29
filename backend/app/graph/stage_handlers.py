"""Real P0/P1 stage handlers — the actual business, run inside LangGraph nodes (R9-5-1, T10/T11).

These replace the inline business in routes_projects.py (complete_onboarding / execute_onboarding /
run_profiling) by delegating to the SAME real services (SourceMaterializer / FullStackProfiler),
so the graph node carries real work — not a stub. The three D-092 reports are produced by StageLoop;
domain artifacts (intake_report / profiler JSONs / summary / p2 manifest) are produced here.

DOC-2: P1 handler returns the REAL identified item list from the profiler result as the single
source of truth (replacing the hardcoded 14-item frontend/route list).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from app.graph.state import GraphState
from app.services.review_pass import ReviewResult
from app.services import workspace_service


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_file_count(project_id: str) -> int:
    src = workspace_service.workspace_path(project_id) / "source"
    if not src.exists():
        return 0
    skip = {".git", "node_modules", "__pycache__"}
    return sum(1 for p in src.rglob("*")
               if p.is_file() and not any(s in skip for s in p.parts))


class RealP0Handler:
    """P0 接入: materialize source → source_index → intake_report. Review: non-manual empty source."""

    goal = "P0 接入：导入源码、登记材料与初始风险，产出可信接入输入"
    acceptance_criteria = [
        "Workspace 已初始化", "来源已识别并尝试物化", "intake 产物已生成",
        "非手动项目源码非空（或明确阻断引导）",
    ]
    planned_actions = ["materialize_source", "generate_source_index", "write_intake_report"]

    def __init__(self, tracer=None, auditor=None):
        self.tracer = tracer
        self.auditor = auditor

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        src_type = state.get("source_type", "manual")
        source_config = state.get("source_config", {}) or {}

        # R9-5-3 T-10: assemble context package at node entry (C0-C6 + SKILL.md bodies)
        context_package: dict = {}
        try:
            from app.services.context_assembler import assemble_context
            context_package = assemble_context(
                project_id, "p0",
                node_state={"node_task": "P0 接入：物化源码 + 登记材料"},
                task_type="onboarding",
            )
        except Exception:
            pass  # context assembly is advisory — domain work proceeds regardless

        materialized = {"materialization_status": "skipped", "file_count": 0}
        try:
            from app.services.source_materializer import SourceMaterializer, generate_source_index
            m = SourceMaterializer(trace_writer=self.tracer, audit_writer=self.auditor)
            materialized = m.materialize(project_id, src_type, source_config)
            if materialized.get("file_count", 0) > 0:
                generate_source_index(project_id)
        except Exception as e:  # honest: record, do not fake success (D-097/公理3)
            materialized = {"materialization_status": "error", "file_count": 0,
                            "errors": [str(e)]}

        file_count = _source_file_count(project_id)

        art_dir = workspace_service.workspace_path(project_id) / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        intake = {
            "artifact_id": f"artifact-intake-{project_id[:8]}",
            "project_id": project_id, "stage": "p0", "artifact_type": "intake_report",
            "source_type": src_type, "file_count": file_count,
            "materialization_status": materialized.get("materialization_status"),
            "generated_at": _now(),
        }
        (art_dir / "intake_report.json").write_text(
            json.dumps(intake, ensure_ascii=False, indent=2), encoding="utf-8")

        assembly_trace = context_package.get("assembly_trace", {})

        return {
            "file_count": file_count,
            "source_type": src_type,
            "materialized": materialized,
            "artifacts": ["artifacts/intake_report.json"],
            "assembly_trace": assembly_trace,
            "evidence_candidates": [
                {"evidence_id": f"ev-p0-ws-{project_id[:8]}",
                 "type": "workspace_initialized", "status": "candidate"},
                {"evidence_id": f"ev-p0-onb-{project_id[:8]}",
                 "type": "onboarding_completed", "status": "candidate"},
            ],
        }

    def review(self, result: dict) -> ReviewResult:
        issues = []
        if result.get("source_type") not in ("manual",) and result.get("file_count", 0) == 0:
            issues.append({"type": "empty_source",
                           "detail": "非手动项目但源码目录为空（应阻断并引导补凭据）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补充源码凭据后重新导入"] if issues else [],
                            reviewer="p0_review_skill")


class RealP1Handler:
    """P1 建档: FullStackProfiler.profile → real identification artifacts + summary + p2 manifest."""

    goal = "P1 建档：全量识别项目结构/技术栈/依赖/配置，产出项目档案与 P2 输入"
    acceptance_criteria = [
        "至少产出 1 项识别产物", "profiling_summary.md 已生成", "p2_input_manifest.json 已生成",
    ]
    planned_actions = ["full_stack_profile", "build_summary", "build_p2_manifest"]

    def __init__(self, tracer=None, auditor=None):
        self.tracer = tracer
        self.auditor = auditor

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]

        # R9-5-3 T-10: context assembly hook (advisory)
        context_package: dict = {}
        try:
            from app.services.context_assembler import assemble_context
            context_package = assemble_context(
                project_id, "p1",
                node_state={"node_task": "P1 建档：全量识别项目结构/技术栈/依赖/配置"},
                task_type="profiling",
            )
        except Exception:
            pass  # advisory — domain work proceeds regardless

        from app.services.full_stack_profiler import FullStackProfiler
        profiler = FullStackProfiler(trace_writer=self.tracer, audit_writer=self.auditor)
        result = profiler.profile(project_id)

        art_dir = workspace_service.workspace_path(project_id) / "artifacts"
        # real produced artifact refs (single source of truth, DOC-2)
        refs: List[str] = []
        identified_items: List[str] = []
        if art_dir.exists():
            for p in sorted(art_dir.glob("*.json")):
                refs.append(f"artifacts/{p.name}")
                if p.name not in ("p2_input_manifest.json",) and not p.name.startswith("p0_") \
                        and not p.name.startswith("p1_") and p.name != "intake_report.json":
                    identified_items.append(p.stem)
            if (art_dir / "profiling_summary.md").exists():
                refs.append("artifacts/profiling_summary.md")

        return {**result, "artifacts": refs, "identified_items": identified_items,
                "assembly_trace": context_package.get("assembly_trace", {})}

    def review(self, result: dict) -> ReviewResult:
        issues = []
        if result.get("items_completed", 0) < 1:
            issues.append({"type": "no_artifacts", "detail": "profiler 未产出识别产物"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["重新执行全量识别并确认产物写入"] if issues else [],
                            reviewer="p1_review_skill")


_bootstrapped = False


def bootstrap_graph_handlers(force: bool = False) -> None:
    """Register real P0/P1 handlers + real Gate backend + writers for the app graph.

    Idempotent. Called at app startup. Tests do NOT call this (they inject fakes).
    """
    global _bootstrapped
    if _bootstrapped and not force:
        return
    from app.graph import nodes
    from app.graph.gate_backend import RealGateBackend
    from app.dependencies import get_services
    svc = get_services()
    nodes.set_tracer_auditor(svc.trace_writer, svc.audit_writer)
    nodes.register_handler("p0", RealP0Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p1", RealP1Handler(svc.trace_writer, svc.audit_writer))
    nodes.set_gate_backend(RealGateBackend())
    _bootstrapped = True
