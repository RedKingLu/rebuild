"""Real P0/P1/P2/P3 stage handlers — the actual business, run inside LangGraph nodes.

P0/P1 (R9-5-1) delegate to SourceMaterializer / FullStackProfiler. P2 (R10 T11)
delegates to AssessmentService (model-driven risk/feasibility). P3 (R10 T17)
delegates to PlanningService (Stage Plan → Task Plan(Batch) → TaskGraph 必生 →
§5.6 Artifacts + §5.7 Evidence). So each graph node carries real work — not a stub.
The three D-092 reports are produced by StageLoop; domain artifacts here.
P4 (R11-3-C5) runs a REAL platform-internal execution link: it loads the P3 TaskGraph
and drives execution nodes through TaskGraphEngine + NodeLoop (9-step, edge strategies
success/failure/retry/rework/gate). Each execution node's Node Worker is
P4ExecutionWorker.execute_node — reads source/ (read-only), writes output_code/ +
patches/ via WorkspaceMediator (D-099/D-104), derives Evidence from the real files;
NodeLoop runs the independent Acceptance. The engine's _finalize honestly classifies
failed/blocked/waiting_gate (never fakes completed). External-agent delegation (C6) and
StagePageP4 (C8) are out of scope. P5-P6 stay future_r11 stubs.

DOC-2: P1 handler returns the REAL identified item list from the profiler result as the single
source of truth (replacing the hardcoded 14-item frontend/route list).
"""

from __future__ import annotations

import hashlib
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


def _mediated_write(project_id: str, rel_path: str, content: str, *,
                    auditor=None, stage: str, action: str) -> str:
    """Write a stage report / artifact through WorkspaceMediator (the single write
    gate, D-099⑥) + record an Audit (L2). source/ is unconditionally rejected and
    escapes raise ValueError. Returns rel_path. Every execution actor writes via the
    mediator — stage handlers no longer bypass it with raw write_text (B-P4-MEDIATOR-BYPASS).
    """
    from app.services.workspace_mediator import WorkspaceMediator
    mediator = WorkspaceMediator(str(workspace_service.workspace_path(project_id)))
    target, risk = mediator.check_write(rel_path)  # raises ValueError on source/ or escape
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if auditor:
        try:
            auditor.write("workspace_write", risk_level=risk, action=action,
                          decision="allowed", reason=f"wrote {rel_path}",
                          project_id=project_id, stage=stage, transition_mode="real")
        except Exception:
            logger.warning("stage report audit write failed (advisory)", exc_info=True)
    return rel_path


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
        _mediated_write(project_id, "artifacts/intake_report.json",
                        json.dumps(intake, ensure_ascii=False, indent=2),
                        auditor=self.auditor, stage="p0", action="write_intake_report")

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
            _mediated_write(project_id, f"artifacts/{name}",
                            json.dumps({"project_id": project_id, "stage": "p2",
                                        "generated_at": _now(), **payload},
                                       ensure_ascii=False, indent=2),
                            auditor=self.auditor, stage="p2", action="write_p2_artifact")
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
        # assessment_report 应为对象；真实 LLM 可返回合法 JSON 但值为字符串/散文（非 dict），
        # 此时 str 无 .get → 归一为 unparseable_output（走 ReviewPass 诚实返工），不崩溃不 500。
        report = result.get("assessment_report")
        if not isinstance(report, dict):
            report = {} if report in (None, "") else {"raw": report, "parse_error": True}
        # unparseable model output → retry structured output (ReviewPass rework)
        if report.get("parse_error"):
            issues.append({"type": "unparseable_output",
                           "detail": "模型输出 assessment_report 非结构化对象，无法解析 6 类产出"})
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
            _mediated_write(project_id, f"artifacts/{name}",
                            json.dumps({"project_id": project_id, "stage": "p3",
                                        "generated_at": _now(), **payload},
                                       ensure_ascii=False, indent=2),
                            auditor=self.auditor, stage="p3", action="write_p3_artifact")
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


class RealP4Handler:
    """P4 执行（R11-3-C5 TaskGraph 执行链路）: 加载 P3 TaskGraph，经 TaskGraphEngine + NodeLoop
    按边策略推进 execution 节点。每个 execution 节点的 Node Worker = P4ExecutionWorker.execute_node
    （读 source/ 只读→经 WorkspaceMediator 写 output_code/+patches/(D-099/D-104)→真实文件派生
    Evidence，非 LLM 自报）；NodeLoop 9 步内含独立 Acceptance；边策略 success/failure/retry/rework/gate
    由 engine 路由，有界重试超阈诚实降级/Gate。

    诚实状态（§5.10 / 公理3 / D-097 / V10 教训，绝不伪造 completed）：
      - P3 TaskGraph 未找到或无 execution 节点 → blocked（缺前置输入）。
      - engine _finalize 诚实归类：任一节点 failed→graph failed；waiting_gate→graph waiting_gate；
        blocked→graph blocked；仅全部 completed/skipped→completed。
      - handler status：graph_status==completed→completed，其余→blocked（交 StageLoop 升级 Gate）。
    P4 handler 由 make_work_node 在 LangGraph p4_work 节点内调用，engine 是该节点的内部编排，不绕
    主编排（D-037）。C5 只准备 P4→P5 Gate（由 make_work_node 在 review passed 时建），不推进 P5 业务。
    C5 不接外部编程 Agent（C6）、不做 StagePageP4（C8）。node_type 路由依 R11-3-C2：execution。
    """

    goal = "P4 执行：经 TaskGraphEngine+NodeLoop 按边策略推进 P3 TaskGraph execution 节点，产出真实 output_code/patches 并过 Acceptance"
    acceptance_criteria = [
        "已加载 P3 TaskGraph 与 execution 节点",
        "经 TaskGraphEngine+NodeLoop 执行至少一个真实 execution 节点，产出 output_code+patch/diff",
        "NodeLoop 9 步 / Acceptance / 边策略均有证据（engine_events）",
        "Evidence 来自真实落盘文件；source/ 未被修改，写 source 负路径被拒记 Audit",
        "失败/阻塞/Gate 路径诚实归类，不静默、不伪造完成",
        "只准备 P4→P5 Gate，不推进 P5 业务",
    ]
    planned_actions = ["load_p3_task_graph", "build_node_executors",
                       "run_task_graph_engine", "nodeloop_9step_per_node",
                       "acceptance_per_node", "aggregate_real_evidence"]

    def __init__(self, tracer=None, auditor=None, gateway=None, aet=None):
        self.tracer = tracer
        self.auditor = auditor
        self._gateway = gateway
        self._aet = aet

    def _services(self):
        from app.dependencies import get_services
        return get_services()

    def _build_worker(self, project_id: str):
        from app.services.p4_execution_worker import P4ExecutionWorker
        gateway = self._gateway
        aet = self._aet
        if gateway is None or aet is None:
            svc = self._services()
            gateway = gateway if gateway is not None else svc.model_gateway
            aet = aet if aet is not None else svc.aet_service
        return P4ExecutionWorker(project_id, tracer=self.tracer, auditor=self.auditor,
                                 aet=aet, gateway=gateway)

    def _load_p3_task_graph(self, project_id: str) -> dict | None:
        """Load the latest P3 TaskGraph (definition) + its nodes for this project.

        Returns None when no P3 TaskGraph exists (P3 未完成 / 未生成). Read-only;
        follows the version-then-recency ordering used by the workspace aggregate.
        """
        from app.core.database import get_session
        from app.models.task_graph import TaskGraph, TaskNode
        db = get_session()
        try:
            tg = (db.query(TaskGraph)
                  .filter(TaskGraph.project_id == project_id, TaskGraph.stage == "p3")
                  .order_by(TaskGraph.version.desc(), TaskGraph.created_at.desc())
                  .first())
            if tg is None:
                return None
            tns = (db.query(TaskNode)
                   .filter(TaskNode.task_graph_id == tg.task_graph_id)
                   .order_by(TaskNode.created_at).all())
            nodes = [{"node_id": n.node_id, "node_type": n.node_type,
                      "title": n.title, "risk_level": n.risk_level,
                      "input_refs": n.input_refs or [],
                      "permission_boundary": n.permission_boundary or "workspace_read",
                      "stage": "p4"} for n in tns]
            return {"task_graph_id": tg.task_graph_id, "stage_plan_ref": tg.stage_plan_ref,
                    "graph_status": tg.graph_status, "edges": tg.edges or [],
                    "edge_count": len(tg.edges or []), "nodes": nodes}
        finally:
            db.close()

    async def execute(self, state: GraphState) -> dict:
        project_id = state["project_id"]

        # 读取 project/run/stage context（advisory，与 P2/P3 一致；task_type=execution）
        context_package: dict = {}
        try:
            from app.services.context_assembler import assemble_context
            context_package = assemble_context(
                project_id, "p4",
                node_state={"node_task": "P4 执行：按 P3 TaskGraph 执行 execution 节点"},
                task_type="execution")
        except Exception:
            logger.warning("P4 context assembly failed (advisory, skeleton proceeds)",
                           exc_info=True)  # 公理3: surface, not silent

        assembly_trace = context_package.get("assembly_trace", {})
        tg = self._load_p3_task_graph(project_id)
        if tg is None:
            return {"status": "blocked",
                    "reason": "P3 TaskGraph 未找到（P3 未完成或未生成），P4 无法执行",
                    "task_graph_ref": None, "assembly_trace": assembly_trace,
                    "artifacts": [], "evidence_refs": []}

        node_type_dist: dict = {}
        for n in tg["nodes"]:
            node_type_dist[n["node_type"]] = node_type_dist.get(n["node_type"], 0) + 1
        exec_nodes = [n for n in tg["nodes"] if n["node_type"] == "execution"]
        if not exec_nodes:
            return {"status": "blocked",
                    "reason": f"P3 TaskGraph {tg['task_graph_id']} 无 execution 节点"
                              f"（node_type 分布 {node_type_dist}）",
                    "task_graph_ref": tg["task_graph_id"], "assembly_trace": assembly_trace,
                    "artifacts": [], "evidence_refs": []}

        # C5：把 execution 节点接入 TaskGraphEngine + NodeLoop，按 TaskGraph 边策略推进。
        # 每个 execution 节点的 Node Worker = P4ExecutionWorker.execute_node（真实写闭环）；
        # 非 execution 节点走 engine 默认执行器。NodeLoop 9 步内含 Acceptance；边策略
        # success/failure/retry/rework/gate 由 engine 路由；失败/阻塞/Gate 经 _finalize 诚实
        # 归类（绝不伪造 completed）。engine 由 handler 在 LangGraph p4_work 节点内调用（D-037）。
        run_id = state.get("run_id", "")
        worker = self._build_worker(project_id)

        # P3 TaskNode 无 acceptance_criteria 列 → NodeLoop step1 会因"缺 acceptance_criteria"
        # 阻塞。为 execution 节点注入默认 P4 结构化验收标准（由真实产物落盘背书），worker 据此
        # 产出 criteria_met 覆盖映射。注入到 tg["nodes"] 的同一节点对象，engine 与 worker 共享。
        _P4_CRITERIA = ["产出 output_code 产物", "产出 patch/diff", "Evidence 来自真实落盘文件"]
        for n in exec_nodes:
            if not n.get("acceptance_criteria"):
                n["acceptance_criteria"] = list(_P4_CRITERIA)

        def _make_exec(nd: dict):
            async def _fn():
                return await worker.execute_node(nd, run_id=run_id)
            return _fn
        node_executors = {n["node_id"]: _make_exec(n) for n in exec_nodes}

        from app.services.task_graph_service import TaskGraphEngine
        mode = workspace_service.get_execution_mode(project_id)
        engine = TaskGraphEngine(tracer=self.tracer, auditor=self.auditor)
        eng = await engine.execute({"nodes": tg["nodes"], "edges": tg["edges"]},
                                   node_executors=node_executors, project_id=project_id,
                                   run_id=run_id, mode=mode)

        # 证据/产物以真实落盘为准（D-066）：从 aet 汇集本 stage 的 Evidence + 其中的产物引用。
        aet = self._aet if self._aet is not None else self._services().aet_service
        try:
            p4_ev = [e for e in aet.list_evidence(project_id, stage="p4")]
        except Exception:
            logger.warning("P4 evidence 汇集失败（advisory）", exc_info=True)
            p4_ev = []
        evidence_refs = [e.get("evidence_id") for e in p4_ev if e.get("evidence_id")]
        artifacts: list[str] = []
        patch_refs: list[str] = []
        for e in p4_ev:
            if e.get("output_code_ref"):
                artifacts.append(e["output_code_ref"])
            if e.get("patch_ref"):
                artifacts.append(e["patch_ref"]); patch_refs.append(e["patch_ref"])
        acceptance_results = [{"node_id": nid, **((r or {}).get("acceptance") or {})}
                              for nid, r in eng.node_results.items()]

        # C7: write a structured P4 execution-summary report (change manifest + patch index +
        # per-node results) — the primary readable review material attached to the P4→P5 Gate.
        summary_ref = self._write_execution_summary(
            project_id, tg, exec_nodes, eng, p4_ev, patch_refs,
            node_type_dist, acceptance_results)

        # graph_status → handler status：completed→completed；其余（failed/waiting_gate/blocked/
        # rework_required）→ blocked（诚实非 completed，交 StageLoop 升级 Gate / 准备 P4→P5 Gate）。
        status = "completed" if eng.graph_status == "completed" else "blocked"

        return {
            "status": status,
            "graph_status": eng.graph_status,
            "reason": eng.reason or f"P4 TaskGraph 执行结果：{eng.graph_status}",
            "task_graph_ref": tg["task_graph_id"],
            "stage_plan_ref": tg["stage_plan_ref"],
            "node_count": len(tg["nodes"]),
            "execution_node_count": len(exec_nodes),
            "completed_node_count": len(eng.completed_nodes),
            "failed_node_count": len(eng.failed_nodes),
            "gated_node_count": len(eng.gated_nodes),
            "completed_nodes": eng.completed_nodes,
            "failed_nodes": eng.failed_nodes,
            "gated_nodes": eng.gated_nodes,
            "node_type_distribution": node_type_dist,
            "edge_count": tg["edge_count"],
            "engine_events": eng.events,
            "acceptance_results": acceptance_results,
            "artifacts": artifacts + ([summary_ref] if summary_ref else []),
            "evidence_refs": evidence_refs,
            "patch_refs": patch_refs,
            "assembly_trace": assembly_trace,
        }

    def _file_facts(self, project_id: str, rel_path: str) -> dict:
        """Re-read a real workspace file through the mediator and return sha256/bytes
        (honest evidence basis = real_file_on_disk). Empty dict if unreadable."""
        from app.services.workspace_service import workspace_path
        from app.services.workspace_mediator import WorkspaceMediator
        try:
            target = WorkspaceMediator(str(workspace_path(project_id))).guard_read(rel_path)
            raw = target.read_bytes()
            return {"path": rel_path,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw)}
        except Exception:
            return {"path": rel_path, "sha256": "", "bytes": 0}

    def _write_execution_summary(self, project_id, tg, exec_nodes, eng, p4_evidence,
                                 patch_refs, node_type_dist,
                                 acceptance_results) -> str | None:
        """C7: persist a structured P4 execution summary to artifacts/p4_execution_summary.json.

        Contains a change manifest (output_code files with real sha256/bytes), a patch index,
        and per-node execution results — all derived from real on-disk files (never the model's
        self-report). Returns the relative ref, or None if nothing to report.
        """
        evidence_refs = [e.get("evidence_id") for e in (p4_evidence or [])
                         if e.get("evidence_id")]
        artifacts = [e["output_code_ref"] for e in (p4_evidence or [])
                     if e.get("output_code_ref")]
        if not (artifacts or patch_refs or evidence_refs):
            return None
        from app.services.workspace_service import workspace_path
        from datetime import datetime, timezone

        def _now():
            return datetime.now(timezone.utc).isoformat()

        out_dir = workspace_path(project_id) / "artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Map output_code_ref → evidence_id so each output file carries its evidence links.
        ev_by_output = {e["output_code_ref"]: e.get("evidence_id")
                        for e in (p4_evidence or []) if e.get("output_code_ref")}
        change_manifest = []
        seen_out: set[str] = set()
        for rel in artifacts:
            if rel in seen_out or not rel.startswith("output_code/"):
                continue
            seen_out.add(rel)
            facts = self._file_facts(project_id, rel)
            ev = ev_by_output.get(rel)
            change_manifest.append({**facts, "kind": "output_code",
                                   "evidence_refs": [ev] if ev else []})
        # Map patch path → source_ref, taken from the real Evidence objects.
        src_by_patch = {e["patch_ref"]: e.get("source_ref")
                        for e in (p4_evidence or [])
                        if e.get("patch_ref") and e.get("source_ref")}
        patch_index = []
        for rel in dict.fromkeys(patch_refs):
            facts = self._file_facts(project_id, rel)
            patch_index.append({**facts, "kind": "patch",
                               "source_ref": src_by_patch.get(rel)})

        node_map = {n["node_id"]: n for n in tg.get("nodes", [])}
        per_node = []
        for nid in list(eng.completed_nodes) + list(eng.failed_nodes) + list(eng.gated_nodes):
            ndef = node_map.get(nid, {})
            res = (eng.node_results or {}).get(nid) or {}
            per_node.append({
                "node_id": nid,
                "title": ndef.get("title") or nid,
                "node_type": ndef.get("node_type") or "execution",
                "status": res.get("node_status") or res.get("status") or "unknown",
                "router": res.get("next_route", ""),
                "node_run_id": res.get("task_node_run_id"),
            })

        summary = {
            "stage": "p4",
            "kind": "execution_summary",
            "generated_at": _now(),
            "graph_id": tg.get("task_graph_id"),
            "graph_status": eng.graph_status,
            "run_id": getattr(eng, "run_id", ""),
            "node_count": len(tg.get("nodes", [])),
            "execution_node_count": len(exec_nodes),
            "completed_node_count": len(eng.completed_nodes),
            "failed_node_count": len(eng.failed_nodes),
            "gated_node_count": len(eng.gated_nodes),
            "blocked_node_count": (len(eng.node_results) - len(eng.completed_nodes)
                                   - len(eng.failed_nodes) - len(eng.gated_nodes)),
            "node_type_distribution": node_type_dist,
            "change_manifest": change_manifest,
            "patch_index": patch_index,
            "nodes": per_node,
            "evidence_refs": evidence_refs,
        }
        out = out_dir / "p4_execution_summary.json"
        ref = _mediated_write(project_id, f"artifacts/{out.name}",
                              json.dumps(summary, ensure_ascii=False, indent=2),
                              auditor=self.auditor, stage="p4",
                              action="write_execution_summary")
        logger.info("C7: wrote P4 execution summary %s (%d output_code, %d patches)",
                    ref, len(change_manifest), len(patch_refs))
        return ref

    def review(self, result: dict) -> ReviewResult:
        status = result.get("status")
        if status != "completed":
            reason = result.get("reason") or f"P4 未完成（status={status}）"
            rec = ("P3 未产出 TaskGraph：先完成 P3 规划并通过 P3→P4 Gate" if status == "blocked"
                   and not result.get("task_graph_ref")
                   else "检查模型可用性 / 节点产物 / Acceptance 后重试")
            return ReviewResult(passed=False,
                                issues=[{"type": "p4_execution_not_completed", "detail": reason}],
                                recommendations=[rec], reviewer="p4_review_skill")
        # completed：须有真实产物 + Evidence（D-066），否则不放行（不伪造完成）。
        issues = []
        if not result.get("artifacts"):
            issues.append({"type": "no_artifacts", "detail": "P4 未产出 output_code/patch 产物"})
        if not result.get("evidence_refs"):
            issues.append({"type": "no_evidence", "detail": "P4 Evidence 未落库（D-066）"})
        return ReviewResult(passed=not issues, issues=issues,
                            recommendations=["补齐执行产物与 Evidence 后重试"] if issues else [],
                            reviewer="p4_review_skill")


def bootstrap_graph_handlers(force: bool = False) -> None:
    """Register real P0/P1/P2/P3 handlers + P4 skeleton + real Gate backend + writers.

    Idempotent. Called at app startup. Tests do NOT call this directly (conftest
    re-bootstraps with test services). P4 is a C5 TaskGraph execution handler (loads P3
    TaskGraph, drives execution nodes via TaskGraphEngine+NodeLoop → output_code/+patches/
    + Evidence + Acceptance + edge strategies; honest failed/blocked/waiting_gate — never
    fakes completed. External-agent delegation lands in C6). P5-P6 stay future_r11 stubs.
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
    nodes.register_handler("p4", RealP4Handler(svc.trace_writer, svc.audit_writer))
    nodes.set_gate_backend(RealGateBackend())
    _bootstrapped = True
