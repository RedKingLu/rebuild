"""Real P0/P1/P2/P3 stage handlers — the actual business, run inside LangGraph nodes.

P0/P1 (R9-5-1) delegate to SourceMaterializer / FullStackProfiler. P2 (R10 T11)
delegates to AssessmentService (model-driven risk/feasibility). P3 (R10 T17)
delegates to PlanningService (Stage Plan → Task Plan(Batch) → TaskGraph 必生 →
§5.6 Artifacts + §5.7 Evidence). So each graph node carries real work — not a stub.
The three D-092 reports are produced by StageLoop; domain artifacts here.
P4-P6 stay future_r11 stubs (Q-R10-3).

DOC-2: P1 handler returns the REAL identified item list from the profiler result as the single
source of truth (replacing the hardcoded 14-item frontend/route list).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from app.graph.state import GraphState
from app.services.review_pass import ReviewResult
from app.services import workspace_service

logger = logging.getLogger("rebuild.stage_handlers")


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


class RealP2Handler:
    """P2 评估: AssessmentService (LLM) → 6 类评估产出 + §4.6 Artifact + §4.7 Evidence 落库.

    Runs inside the LangGraph P2 node via StageLoop (D-091). The assessment is
    genuinely model-driven (Q-R10-2): no model Key → status=blocked → review fails
    → the node escalates to a Gate honestly (never a fake completed). Model output
    is auxiliary analysis, not fact (analysis_only, §4.4-8/§4.7-5/STOP-4). P2 does
    NOT execute a TaskGraph — that is P3/P4 (no NodeLoop here).
    """

    goal = "P2 评估：识别迁移/重构风险、阻塞项、不确定项、验证缺口与资源需求，为 P3 规划提供可信依据"
    acceptance_criteria = [
        "风险清单已生成", "阻塞项与不确定项已登记", "验证缺口已登记",
        "资源需求建议已生成或明确无需求", "P2 Evidence 已落库（§4.7 五项，D-066）",
        "模型输出已标记为辅助分析而非事实（analysis_only）",
    ]
    planned_actions = ["assess_with_llm", "write_p2_artifacts", "persist_p2_evidence"]

    def __init__(self, tracer=None, auditor=None, assessment_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._svc = assessment_service

    def _service(self):
        if self._svc is not None:
            return self._svc
        from app.services.assessment_service import AssessmentService
        return AssessmentService(tracer=self.tracer, auditor=self.auditor)

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        run_id = state.get("run_id", "")
        user_goal = state.get("user_goal") or state.get("run_goal") or ""

        # R10-5 P1-C: assemble C0-C6 context + build system prompt (C3 Skill metadata
        # only — progressive disclosure). The assessment is driven through the unified
        # context path, not an ad-hoc hardcoded prompt (S4). Advisory on failure.
        context_package: dict = {}
        system_prompt: str | None = None
        try:
            from app.services.context_assembler import assemble_context, build_system_prompt
            node_state = {"node_task": "P2 评估：识别迁移/重构风险、阻塞项、验证缺口与资源需求",
                          "task": "评估可行性与风险"}
            context_package = assemble_context(
                project_id, "p2", node_state=node_state,
                task_type="assessment", skill_disclosure="metadata")
            system_prompt = build_system_prompt(
                project_id, "p2", node_state=node_state,
                task_type="assessment", skill_disclosure="metadata")
        except Exception:
            logger.warning("P2 context assembly failed (advisory, domain work proceeds)",
                           exc_info=True)  # 公理3: surface, not silent

        svc = self._service()
        result = await svc.assess(project_id, run_id=run_id, stage="p2", user_goal=user_goal,
                                  system_prompt=system_prompt, context_package=context_package)

        artifacts: List[str] = []
        evidence_refs: List[str] = []
        # §4.6 Artifact + §4.10 只在真正完成时落产物/证据 (blocked/failed 不伪造 completed)
        if result.status == "completed":
            artifacts = self._write_artifacts(project_id, result)
            persisted = svc.persist_evidence(project_id, result, stage="p2")
            evidence_refs = [e.get("evidence_id") for e in persisted]

        return {
            "status": result.status,
            "reason": result.reason,
            "analysis_only": result.analysis_only,
            "assessment_report": result.assessment_report,
            "risk_list": result.risk_list,
            "blocker_list": result.blocker_list,
            "uncertainty_list": result.uncertainty_list,
            "validation_gap_list": result.validation_gap_list,
            "resource_needs": result.resource_needs,
            "model_used": result.model_used,
            "artifacts": artifacts,
            "evidence_refs": evidence_refs,
            "context_refs": result.context_refs,
            "skill_refs": result.skill_refs,
            "assembly_trace": context_package.get("assembly_trace", {}),
        }

    def _write_artifacts(self, project_id: str, result) -> List[str]:
        """§4.6: 风险评估 / 阻塞项 / 验证缺口 / 资源需求 Artifact + §4.5 评估报告."""
        art_dir = workspace_service.workspace_path(project_id) / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "p2_assessment_report.json": {
                "artifact_type": "assessment_report", "analysis_only": result.analysis_only,
                "report": result.assessment_report, "uncertainty_list": result.uncertainty_list},
            "p2_risk_list.json": {"artifact_type": "risk_assessment", "items": result.risk_list},
            "p2_blocker_list.json": {"artifact_type": "blocker_list", "items": result.blocker_list},
            "p2_validation_gaps.json": {"artifact_type": "validation_gap", "items": result.validation_gap_list},
            "p2_resource_needs.json": {"artifact_type": "resource_needs", "items": result.resource_needs},
        }
        refs: List[str] = []
        for name, payload in files.items():
            (art_dir / name).write_text(
                json.dumps({"project_id": project_id, "stage": "p2",
                            "generated_at": _now(), **payload}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            refs.append(f"artifacts/{name}")
        return refs

    def review(self, result: dict) -> ReviewResult:
        status = result.get("status")
        # blocked/failed → not passed; StageLoop escalates to a Gate (honest, §4.10)
        if status != "completed":
            reason = result.get("reason") or f"P2 评估未完成（status={status}）"
            rec = "配置有效模型 Key 后重试" if status == "blocked" else "检查模型调用与输入后重试"
            return ReviewResult(passed=False, issues=[{"type": "assessment_not_completed",
                                                       "detail": reason}],
                                recommendations=[rec], reviewer="p2_review_skill")
        issues = []
        report = result.get("assessment_report") or {}
        # unparseable model output → retry structured output (ReviewPass rework)
        if report.get("parse_error"):
            issues.append({"type": "unparseable_output",
                           "detail": "模型输出非结构化，无法解析 6 类产出"})
        # §4.9-6 / D-066: Evidence must be persisted
        if not result.get("evidence_refs"):
            issues.append({"type": "no_evidence", "detail": "P2 Evidence 未落库（D-066）"})
        # §4.7-5 / STOP-4: model output must be marked analysis_only
        if not result.get("analysis_only"):
            issues.append({"type": "model_output_not_marked",
                           "detail": "模型输出未标记为 analysis_only（§4.7-5）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补齐评估产出与 Evidence 后重试"] if issues else [],
                            reviewer="p2_review_skill")


class RealP3Handler:
    """P3 规划: PlanningService → Stage Plan → Task Plan(Batch) → TaskGraph(必生) →
    §5.6 Artifacts + §5.7 Evidence. Runs inside the LangGraph P3 node via StageLoop.

    execute chains T13→T14→T15→T16. Q-R10-2: no model Key → blocked → review fails
    → escalate to a Gate honestly (§5.10). Q-R10-3: a TaskGraph is ALWAYS generated.
    P4 is NOT registered (future_r11 stub); the P3→P4 Gate is created by
    make_work_node when this handler's loop passes.
    """

    goal = "P3 规划：基于 P2 评估生成 Stage Plan / Task Plan(Batch) / TaskGraph（必生）与执行/验证策略，供 P3→P4 Gate 审核"
    acceptance_criteria = [
        "Stage Plan 已生成", "Task Plan(Batch) 已生成", "TaskGraph 已生成（Q-R10-3 必生）",
        "边策略显式且通过校验", "P3 Evidence 已落库（§5.7 六项，D-066）", "高风险动作已标识",
    ]
    planned_actions = ["generate_stage_plan", "generate_task_plans", "generate_task_graph",
                       "write_p3_artifacts", "persist_p3_evidence"]

    def __init__(self, tracer=None, auditor=None, planning_service=None):
        self.tracer = tracer
        self.auditor = auditor
        self._svc = planning_service

    def _service(self):
        if self._svc is not None:
            return self._svc
        from app.services.planning_service import PlanningService
        return PlanningService(tracer=self.tracer, auditor=self.auditor)

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]
        run_id = state.get("run_id", "")
        user_goal = state.get("user_goal") or state.get("run_goal") or ""
        svc = self._service()

        # R10-5 P1-C: assemble C0-C6 context + system prompt (C3 Skill metadata only —
        # progressive disclosure) ONCE for the stage; threaded into all three planning
        # sub-calls so P3 runs through the unified context path (S4). Advisory on failure.
        context_package: dict = {}
        system_prompt: str | None = None
        try:
            from app.services.context_assembler import assemble_context, build_system_prompt
            node_state = {"node_task": "P3 规划：生成 Stage Plan / Task Plan / TaskGraph（必生）",
                          "task": "迁移方案与任务图规划"}
            context_package = assemble_context(
                project_id, "p3", node_state=node_state,
                task_type="planning", skill_disclosure="metadata")
            system_prompt = build_system_prompt(
                project_id, "p3", node_state=node_state,
                task_type="planning", skill_disclosure="metadata")
        except Exception:
            logger.warning("P3 context assembly failed (advisory, domain work proceeds)",
                           exc_info=True)  # 公理3: surface, not silent
        context_refs, skill_refs = svc.context_refs(context_package)

        # T13 → T14 → T15: each stage's blocked/failed short-circuits honestly (§5.10)
        sp = await svc.generate_stage_plan(project_id, run_id=run_id, stage="p3",
                                           user_goal=user_goal, system_prompt=system_prompt)
        if sp.status != "completed":
            return {"status": sp.status, "reason": sp.reason,
                    "stage_plan_ref": sp.stage_plan_id, "artifacts": [], "evidence_refs": []}
        batch = await svc.generate_task_plans(project_id, sp.stage_plan_id,
                                              run_id=run_id, stage="p3", user_goal=user_goal,
                                              system_prompt=system_prompt)
        if batch.status != "completed":
            return {"status": batch.status, "reason": batch.reason,
                    "stage_plan_ref": sp.stage_plan_id, "artifacts": [], "evidence_refs": []}
        tg = await svc.generate_task_graph(project_id, sp.stage_plan_id,
                                           run_id=run_id, stage="p3", user_goal=user_goal,
                                           system_prompt=system_prompt)
        if tg.status != "completed":
            return {"status": tg.status, "reason": tg.reason,
                    "stage_plan_ref": sp.stage_plan_id, "task_graph_ref": tg.task_graph_id,
                    "artifacts": [], "evidence_refs": []}

        # T16 Evidence + §5.6 Artifacts (only when all three completed)
        persisted = svc.persist_p3_evidence(project_id, sp, batch, tg, stage="p3",
                                            context_refs=context_refs, skill_refs=skill_refs)
        evidence_refs = [e.get("evidence_id") for e in persisted]
        artifacts = self._write_artifacts(project_id, sp, batch, tg)
        return {
            "status": "completed", "stage_plan_ref": sp.stage_plan_id,
            "batch_id": batch.batch_id, "task_graph_ref": tg.task_graph_id,
            "task_plan_ids": batch.task_plan_ids, "degraded": tg.degraded,
            "batch_risk_level": batch.batch_risk_level, "gate_required": batch.gate_required,
            "artifacts": artifacts, "evidence_refs": evidence_refs, "model_used": sp.model_used,
            "context_refs": context_refs, "skill_refs": skill_refs,
            "assembly_trace": context_package.get("assembly_trace", {}),
        }

    def _write_artifacts(self, project_id: str, sp, batch, tg) -> List[str]:
        """§5.6: 方案 / Stage Plan / Task Plan / TaskGraph / Gate 策略 / 验证策略 Artifact."""
        art_dir = workspace_service.workspace_path(project_id) / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "p3_stage_plan.json": {"artifact_type": "stage_plan", "stage_plan_ref": sp.stage_plan_id,
                                   "objective": sp.objective, "scope": sp.scope,
                                   "risk_level": sp.risk_level, "gate_policy": sp.gate_policy,
                                   "completion_criteria": sp.completion_criteria},
            "p3_task_plans.json": {"artifact_type": "task_plan", "batch_id": batch.batch_id,
                                   "task_plan_refs": batch.task_plan_ids,
                                   "batch_risk_level": batch.batch_risk_level,
                                   "gate_required": batch.gate_required},
            "p3_task_graph.json": {"artifact_type": "task_graph", "task_graph_ref": tg.task_graph_id,
                                   "node_count": tg.node_count, "edge_count": tg.edge_count,
                                   "degraded": tg.degraded},
        }
        refs: List[str] = []
        for name, payload in files.items():
            (art_dir / name).write_text(
                json.dumps({"project_id": project_id, "stage": "p3",
                            "generated_at": _now(), **payload}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            refs.append(f"artifacts/{name}")
        return refs

    def review(self, result: dict) -> ReviewResult:
        status = result.get("status")
        if status != "completed":
            reason = result.get("reason") or f"P3 规划未完成（status={status}）"
            rec = "配置有效模型 Key 后重试" if status == "blocked" else "检查模型调用与输入后重试"
            return ReviewResult(passed=False, issues=[{"type": "planning_not_completed",
                                                       "detail": reason}],
                                recommendations=[rec], reviewer="p3_review_skill")
        issues = []
        if not result.get("stage_plan_ref"):
            issues.append({"type": "no_stage_plan", "detail": "Stage Plan 未生成（§5.9-2）"})
        if not result.get("task_graph_ref"):
            issues.append({"type": "no_task_graph", "detail": "TaskGraph 未生成（Q-R10-3 必生 / §5.9-4）"})
        if not result.get("evidence_refs"):
            issues.append({"type": "no_evidence", "detail": "P3 Evidence 未落库（D-066 / §5.9-8）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补齐 P3 计划产出与 Evidence 后重试"] if issues else [],
                            reviewer="p3_review_skill")


def bootstrap_graph_handlers(force: bool = False) -> None:
    """Register real P0/P1/P2/P3 handlers + real Gate backend + writers for the app graph.

    Idempotent. Called at app startup. Tests do NOT call this (they inject fakes).
    P4-P6 are intentionally NOT registered — they stay future_r11 stubs (Q-R10-3).
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
    nodes.register_handler("p2", RealP2Handler(svc.trace_writer, svc.audit_writer))
    nodes.register_handler("p3", RealP3Handler(svc.trace_writer, svc.audit_writer))
    nodes.set_gate_backend(RealGateBackend())
    _bootstrapped = True
