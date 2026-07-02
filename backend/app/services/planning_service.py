"""P3 planning service (R10 T13-T15).

T13: generate_stage_plan — LLM-driven Stage Plan generation
     (契约 `01-P0-P6阶段契约.md` §5.4-1/2, 规范 `02-...规范.md` §2, fields §5.2 of
     `02-架构设计/03-Project-Run-TaskGraph状态架构.md`).
T14/T15 extend this service with Task Plan and TaskGraph generation.

Design locks (R10-2 §5, user-decided):
  - Q-R10-2: LLM is REQUIRED. No available model → status="blocked" (no rule
    fallback) — an honest blocked beats a fake generated plan.
  - §5.7-1: the plan must derive from the P2 assessment, so the P2 artifacts
    (p2_assessment_report / risk_list / blocker_list / validation_gaps /
    resource_needs) are read as input; missing inputs are recorded, not invented.
  - §5.4 / D-023: the generated Stage Plan is a DRAFT pending the P3→P4 user Gate;
    it is stored plan_status="draft", never auto-approved.

The model is called through ModelGateway (D-098, no hardcoded model/endpoint).
`gateway` is injectable so tests exercise blocked/parse paths without a live LLM.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.status import RISK_LEVELS
from app.services.task_graph_service import EDGE_TYPES

logger = logging.getLogger(__name__)

# §5.2 Stage Plan content fields the model must produce (id/status/audit cols are set by us)
STAGE_PLAN_FIELDS = [
    "objective", "scope", "out_of_scope", "risk_level", "permission_boundary",
    "expected_artifacts", "expected_evidence", "gate_policy", "completion_criteria",
]

_STAGE_PLAN_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P3 规划 Agent。基于 P2 评估产出（评估报告/风险/阻塞/验证缺口/资源需求），"
    "为当前 P 阶段生成一份 Stage Plan（阶段级计划），供用户在 P3→P4 Gate 审核。"
    "严格输出 JSON，键为：objective(字符串,阶段目标), scope(数组,范围内事项), out_of_scope(数组,明确不做), "
    "risk_level(L0-L5), permission_boundary(字符串,权限边界), expected_artifacts(数组), "
    "expected_evidence(数组), gate_policy(对象,哪些情况触发 Gate), completion_criteria(数组,计划覆盖完成条件), "
    "validation_strategy(字符串,P5 验证策略)。要求：方案必须来源于 P2 评估；必须声明 out_of_scope；"
    "高风险动作必须在 risk_level/gate_policy 中显式标识。你生成的是待用户审核的计划草案，"
    "不得替代执行、不得越过 Gate。"
)


_HIGH_RISK = ("L4", "L5")

_TASK_PLAN_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P3 规划 Agent。基于已生成的 Stage Plan，拆解出一批任务级 Task Plan"
    "（Task Plan Batch），供 TaskGraph 承接。严格输出 JSON，键为："
    "batch_objective(字符串), batch_scope(数组), permission_boundary(字符串), "
    "validation_strategy(字符串,批次验收方式), exception_policy(字符串,异常升级策略), "
    "task_plans(数组，每项含 objective(字符串), scope(数组), inputs(数组), expected_outputs(数组), "
    "risk_level(L0-L5), permission_boundary(字符串), required_resources(数组), "
    "model_policy_override(字符串或null), validation_method(字符串), expected_artifacts(数组), "
    "expected_evidence(数组), title(字符串))。硬约束：每个 Task Plan 不得超出 Stage Plan 的 scope；"
    "高风险(L4/L5)任务必须在 risk_level 显式标识；任务必须可追溯到 Stage Plan。"
)

_TASK_GRAPH_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P3 规划 Agent。为给定的任务节点列表设计任务级 TaskGraph 的边（依赖顺序）。"
    "严格输出 JSON：{\"edges\": [{source_index, target_index, edge_type}]}。"
    "edge_type 表达任务间关系（串行 sequence / 并行 parallel / 条件 conditional / 失败 failure / "
    "重试 retry / 返工 rework / 合并 merge 等）。只表达任务依赖，不得让 TaskGraph 越过用户 Gate。"
    "若任务本就线性，用 sequence 单链即可。"
)


@dataclass
class StagePlanResult:
    status: str                       # completed / blocked / failed
    reason: str = ""
    stage_plan_id: Optional[str] = None
    objective: str = ""
    scope: dict = field(default_factory=dict)
    risk_level: str = "L0"
    permission_boundary: str = ""
    expected_artifacts: list = field(default_factory=list)
    expected_evidence: list = field(default_factory=list)
    gate_policy: dict = field(default_factory=dict)
    completion_criteria: list = field(default_factory=list)
    model_used: Optional[str] = None
    parse_error: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status, "reason": self.reason,
            "stage_plan_id": self.stage_plan_id, "objective": self.objective,
            "scope": self.scope, "risk_level": self.risk_level,
            "permission_boundary": self.permission_boundary,
            "expected_artifacts": self.expected_artifacts,
            "expected_evidence": self.expected_evidence,
            "gate_policy": self.gate_policy,
            "completion_criteria": self.completion_criteria,
            "model_used": self.model_used, "parse_error": self.parse_error,
        }


@dataclass
class TaskPlanBatchResult:
    status: str                       # completed / blocked / failed
    reason: str = ""
    batch_id: Optional[str] = None
    stage_plan_ref: Optional[str] = None
    task_plan_ids: list = field(default_factory=list)
    batch_objective: str = ""
    batch_risk_level: str = "L0"
    gate_required: bool = False       # §4.2-4: high-risk task in batch → Gate
    model_used: Optional[str] = None
    parse_error: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status, "reason": self.reason,
            "batch_id": self.batch_id, "stage_plan_ref": self.stage_plan_ref,
            "task_plan_ids": self.task_plan_ids, "batch_objective": self.batch_objective,
            "batch_risk_level": self.batch_risk_level, "gate_required": self.gate_required,
            "model_used": self.model_used, "parse_error": self.parse_error,
        }


@dataclass
class TaskGraphResult:
    status: str                       # completed / blocked / failed
    reason: str = ""
    task_graph_id: Optional[str] = None
    stage_plan_ref: Optional[str] = None
    node_count: int = 0
    edge_count: int = 0
    degraded: bool = False            # True = fell back to single-chain sequence (最简退化)
    validation_errors: list = field(default_factory=list)
    model_used: Optional[str] = None
    parse_error: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status, "reason": self.reason,
            "task_graph_id": self.task_graph_id, "stage_plan_ref": self.stage_plan_ref,
            "node_count": self.node_count, "edge_count": self.edge_count,
            "degraded": self.degraded, "validation_errors": self.validation_errors,
            "model_used": self.model_used, "parse_error": self.parse_error,
        }



class PlanningService:
    def __init__(self, *, gateway=None, tracer=None, auditor=None, aet_service=None):
        self._gateway = gateway
        self.tracer = tracer
        self.auditor = auditor
        self._aet_service = aet_service

    def _get_gateway(self):
        if self._gateway is not None:
            return self._gateway
        from app.dependencies import get_services
        return get_services().model_gateway

    def _get_aet(self):
        if self._aet_service is not None:
            return self._aet_service
        from app.dependencies import get_services
        return get_services().aet_service

    def _db(self):
        from app.core.database import get_session
        return get_session()

    @staticmethod
    def _combine_system(domain_prompt: str, system_prompt: Optional[str]) -> str:
        """R10-5 P1-C: combine assembled context (C0-C6 + C3 Skill metadata) with the
        P3 domain output contract. The contract stays last so the JSON schema
        instruction is preserved (real products, not weakened)."""
        return f"{system_prompt}\n\n---\n\n{domain_prompt}" if system_prompt else domain_prompt

    @staticmethod
    def context_refs(context_package: Optional[dict]) -> tuple[list, list]:
        """Extract (context_refs, skill_refs) from an assembled context package."""
        if not context_package:
            return [], []
        trace = context_package.get("assembly_trace", {}) or {}
        crefs = list(trace.get("layers_assembled", []))
        srefs = [s.get("skill_id") or s.get("name")
                 for s in (context_package.get("skills") or [])
                 if s.get("skill_id") or s.get("name")]
        return crefs, srefs

    # ── T13: Stage Plan generation ──────────────────────────────────────────
    async def generate_stage_plan(
        self,
        project_id: str,
        *,
        run_id: Optional[str] = None,
        stage: str = "p3",
        user_goal: str = "",
        strategy_id: str = "system-default",
        system_prompt: Optional[str] = None,
    ) -> StagePlanResult:
        """Generate a Stage Plan from the P2 assessment via LLM, persist as draft."""
        gw = self._get_gateway()

        # Q-R10-2: no available model → blocked, no rule fallback.
        status = gw.get_status()
        overall = getattr(status, "overall_status", None) or (
            status.get("overall_status") if isinstance(status, dict) else None)
        if overall != "available":
            self._trace("P3 stage-plan blocked: no model", project_id, run_id, stage)
            return StagePlanResult(
                status="blocked",
                reason="no_model_key: 规划需要 LLM 支持，请配置有效 API Key（P3 不降级为规则规划）")

        inputs = self._gather_p2_inputs(project_id, user_goal)
        messages = [
            {"role": "system", "content": self._combine_system(_STAGE_PLAN_SYSTEM_PROMPT, system_prompt)},
            {"role": "user", "content": self._build_user_prompt(inputs)},
        ]
        result = await gw.call(messages=messages, strategy_id=strategy_id,
                               max_tokens=4096, temperature=0.3, source="api")
        if result.get("status") != "completed":
            reason = result.get("error_message") or result.get("error_category") or "model_call_failed"
            self._trace(f"P3 stage-plan model call not completed: {reason}", project_id, run_id, stage)
            return StagePlanResult(status="failed", reason=str(reason), model_used=result.get("model"))

        parsed, parse_error = self._parse(result.get("content", ""))
        model_used = result.get("model")
        sp_id = self._persist_stage_plan(project_id, run_id, stage, parsed, model_used, inputs)
        self._trace("P3 stage-plan generated (draft)", project_id, run_id, stage)
        risk = parsed.get("risk_level", "L0")
        return StagePlanResult(
            status="completed", stage_plan_id=sp_id,
            objective=parsed.get("objective", "") or "",
            scope={"scope": parsed.get("scope", []), "out_of_scope": parsed.get("out_of_scope", [])},
            risk_level=risk if risk in RISK_LEVELS else "L0",
            permission_boundary=parsed.get("permission_boundary", "") or "",
            expected_artifacts=parsed.get("expected_artifacts", []) or [],
            expected_evidence=parsed.get("expected_evidence", []) or [],
            gate_policy=parsed.get("gate_policy", {}) or {},
            completion_criteria=parsed.get("completion_criteria", []) or [],
            model_used=model_used, parse_error=parse_error,
        )

    # ── T14: Task Plan (Batch) generation ────────────────────────────────────
    async def generate_task_plans(
        self,
        project_id: str,
        stage_plan_id: str,
        *,
        run_id: Optional[str] = None,
        stage: str = "p3",
        user_goal: str = "",
        strategy_id: str = "system-default",
        system_prompt: Optional[str] = None,
    ) -> TaskPlanBatchResult:
        """Generate a Task Plan Batch承接 the given Stage Plan, persist as draft rows.

        §3.2-1: stage_plan_ref is REQUIRED — a missing/unknown Stage Plan is a
        failure, not a fabricated plan. §3.2-2: task plans must not expand the
        Stage Plan scope (the scope is fed to the model; violations surface at
        review/Gate). §4.2-3/4: batch risk = max task risk; high-risk → Gate.
        """
        from app.models.stage_plan import StagePlan

        gw = self._get_gateway()
        status = gw.get_status()
        overall = getattr(status, "overall_status", None) or (
            status.get("overall_status") if isinstance(status, dict) else None)
        if overall != "available":
            self._trace("P3 task-plan blocked: no model", project_id, run_id, stage)
            return TaskPlanBatchResult(
                status="blocked", stage_plan_ref=stage_plan_id,
                reason="no_model_key: 规划需要 LLM 支持，请配置有效 API Key（P3 不降级为规则规划）")

        # §3.2-1: load the owning Stage Plan; missing → failed (no fabrication)
        db = self._db()
        try:
            sp = db.get(StagePlan, stage_plan_id)
            if sp is None:
                return TaskPlanBatchResult(
                    status="failed", stage_plan_ref=stage_plan_id,
                    reason=f"stage_plan_not_found: {stage_plan_id}（Task Plan 必须承接已存在的 Stage Plan）")
            sp_scope = sp.scope or {}
            sp_objective = sp.objective or ""
        finally:
            db.close()

        messages = [
            {"role": "system", "content": self._combine_system(_TASK_PLAN_SYSTEM_PROMPT, system_prompt)},
            {"role": "user", "content": self._build_task_plan_prompt(sp_objective, sp_scope, user_goal)},
        ]
        result = await gw.call(messages=messages, strategy_id=strategy_id,
                               max_tokens=4096, temperature=0.3, source="api")
        if result.get("status") != "completed":
            reason = result.get("error_message") or result.get("error_category") or "model_call_failed"
            self._trace(f"P3 task-plan model call not completed: {reason}", project_id, run_id, stage)
            return TaskPlanBatchResult(status="failed", stage_plan_ref=stage_plan_id,
                                       reason=str(reason), model_used=result.get("model"))

        parsed, parse_error = self._parse(result.get("content", ""))
        model_used = result.get("model")
        task_specs = parsed.get("task_plans", []) if isinstance(parsed.get("task_plans"), list) else []
        return self._persist_task_batch(
            project_id, run_id, stage, stage_plan_id, parsed, task_specs, model_used, parse_error)

    def _build_task_plan_prompt(self, sp_objective: str, sp_scope: dict, user_goal: str) -> str:
        return (
            f"Stage Plan 目标：{sp_objective or '（未提供）'}\n"
            f"Stage Plan 范围（不得超出）：{json.dumps(sp_scope, ensure_ascii=False)}\n"
            f"用户目标/约束：{user_goal or '（未提供）'}\n"
            "请据此拆解出 Task Plan Batch（JSON）。每个 Task Plan 必须落在上述范围内。"
        )

    def _persist_task_batch(self, project_id: str, run_id: Optional[str], stage: str,
                            stage_plan_id: str, parsed: dict, task_specs: list,
                            model_used: Optional[str], parse_error: bool) -> TaskPlanBatchResult:
        """Persist each Task Plan row (shared batch_id + stage_plan_ref) and record
        batch metadata on the owning StagePlan.plan_detail (T4: no separate batch table)."""
        from app.models.stage_plan import StagePlan, TaskPlan

        batch_id = f"tpb-{uuid.uuid4().hex[:8]}"
        db = self._db()
        try:
            task_ids: list[str] = []
            risks: list[str] = []
            for spec in task_specs:
                if not isinstance(spec, dict):
                    continue
                risk = spec.get("risk_level", "L0")
                risk = risk if risk in RISK_LEVELS else "L0"
                risks.append(risk)
                mpo = spec.get("model_policy_override")
                tp = TaskPlan(
                    project_id=project_id, run_id=run_id or None,
                    stage=(stage or "p3").lower(),
                    stage_plan_ref=stage_plan_id,          # §3.2-1 traceable
                    batch_id=batch_id,
                    objective=spec.get("objective") or None,
                    scope={"scope": spec.get("scope", [])},
                    inputs=spec.get("inputs", []),
                    expected_outputs=spec.get("expected_outputs", []),
                    risk_level=risk,
                    permission_boundary=spec.get("permission_boundary") or None,
                    required_resources=spec.get("required_resources", []),
                    model_policy_override=mpo if isinstance(mpo, str) else None,
                    validation_method=spec.get("validation_method") or None,
                    expected_artifacts=spec.get("expected_artifacts", []),
                    expected_evidence=spec.get("expected_evidence", []),
                    title=(spec.get("title") or spec.get("objective") or "")[:255],
                    description=spec.get("objective") or None,
                    status="draft",
                )
                db.add(tp)
                db.flush()   # assign task_plan_id
                task_ids.append(tp.task_plan_id)

            # §4.2-3: batch risk = max task risk; §4.2-4: high-risk → Gate
            batch_risk = self._max_risk(risks)
            gate_required = batch_risk in _HIGH_RISK
            batch_meta = {
                "batch_id": batch_id,
                "batch_objective": parsed.get("batch_objective", ""),
                "batch_scope": parsed.get("batch_scope", []),
                "permission_boundary": parsed.get("permission_boundary", ""),
                "validation_strategy": parsed.get("validation_strategy", ""),
                "exception_policy": parsed.get("exception_policy", ""),
                "batch_risk_level": batch_risk,
                "gate_required": gate_required,
                "task_plan_refs": task_ids,
                "model_used": model_used,
            }
            # record batch metadata on the owning Stage Plan (no separate batch table, T4)
            sp = db.get(StagePlan, stage_plan_id)
            if sp is not None:
                detail = dict(sp.plan_detail or {})
                detail["task_plan_batch"] = batch_meta
                sp.plan_detail = detail
            db.commit()
            return TaskPlanBatchResult(
                status="completed", batch_id=batch_id, stage_plan_ref=stage_plan_id,
                task_plan_ids=task_ids, batch_objective=batch_meta["batch_objective"],
                batch_risk_level=batch_risk, gate_required=gate_required,
                model_used=model_used, parse_error=parse_error)
        finally:
            db.close()

    @staticmethod
    def _max_risk(risks: list) -> str:
        """Highest risk level among the batch tasks (§4.2-3). L0 when empty."""
        idx = max((RISK_LEVELS.index(r) for r in risks if r in RISK_LEVELS), default=0)
        return RISK_LEVELS[idx]

    # ── T15: TaskGraph generation (必生, Q-R10-3) ─────────────────────────────
    async def generate_task_graph(
        self,
        project_id: str,
        stage_plan_id: str,
        *,
        run_id: Optional[str] = None,
        stage: str = "p3",
        user_goal: str = "",
        strategy_id: str = "system-default",
        system_prompt: Optional[str] = None,
    ) -> "TaskGraphResult":
        """Generate a TaskGraph for the Stage Plan's Task Plans and persist it.

        Q-R10-3: P3 ALWAYS produces a TaskGraph — no "no-graph + reason" path. Nodes
        are derived deterministically from the Task Plans (TaskGraph 承接 Task Plan,
        §1); the LLM proposes the edge structure + strategy. The proposed edges MUST
        pass T5 validate_edges before persistence — if they don't (or the model
        returns none), the graph DEGRADES to a guaranteed-valid single-chain sequence
        (最简退化) which is marked `degraded=True` (honest, not hidden). An invalid
        graph is NEVER persisted (不完整拒绝入库).
        """
        from app.models.stage_plan import StagePlan, TaskPlan

        gw = self._get_gateway()
        status = gw.get_status()
        overall = getattr(status, "overall_status", None) or (
            status.get("overall_status") if isinstance(status, dict) else None)
        if overall != "available":
            self._trace("P3 task-graph blocked: no model", project_id, run_id, stage)
            return TaskGraphResult(
                status="blocked", stage_plan_ref=stage_plan_id,
                reason="no_model_key: 规划需要 LLM 支持，请配置有效 API Key（P3 不降级为规则规划）")

        db = self._db()
        try:
            sp = db.get(StagePlan, stage_plan_id)
            if sp is None:
                return TaskGraphResult(status="failed", stage_plan_ref=stage_plan_id,
                                       reason=f"stage_plan_not_found: {stage_plan_id}")
            tps = (db.query(TaskPlan)
                   .filter(TaskPlan.stage_plan_ref == stage_plan_id)
                   .order_by(TaskPlan.created_at).all())
            # pre-assign node ids so edges can reference them before persist
            nodes = [{
                "node_id": f"tn-{uuid.uuid4().hex[:8]}", "index": i,
                "task_plan_ref": t.task_plan_id,
                "title": (t.title or t.objective or f"task {i}")[:255],
                "risk_level": t.risk_level if t.risk_level in RISK_LEVELS else "L0",
                "permission_boundary": t.permission_boundary,
                "model_policy_override": t.model_policy_override,
                "required_resources": t.required_resources or [],
                "input_refs": t.inputs or [],
            } for i, t in enumerate(tps)]
        finally:
            db.close()

        # 必生 needs task source: no Task Plans → cannot form a graph → failed
        if not nodes:
            return TaskGraphResult(
                status="failed", stage_plan_ref=stage_plan_id,
                reason="no_task_plans: TaskGraph 承接 Task Plan，无任务计划无法生成（先跑 T14）")

        proposed, model_used, parse_error, call_status, reason = await self._propose_edges(
            nodes, user_goal, strategy_id, system_prompt=system_prompt)
        if call_status == "failed":
            return TaskGraphResult(status="failed", stage_plan_ref=stage_plan_id,
                                   reason=reason, model_used=model_used)

        edges, degraded, val_errors = self._build_validated_edges(nodes, proposed)
        graph_id = self._persist_task_graph(
            project_id, run_id, stage, stage_plan_id, nodes, edges, model_used)
        self._trace(f"P3 task-graph generated ({'degraded single-chain' if degraded else 'llm'})",
                    project_id, run_id, stage)
        return TaskGraphResult(
            status="completed", task_graph_id=graph_id, stage_plan_ref=stage_plan_id,
            node_count=len(nodes), edge_count=len(edges), degraded=degraded,
            validation_errors=val_errors, model_used=model_used, parse_error=parse_error)

    async def _propose_edges(self, nodes: list, user_goal: str, strategy_id: str,
                             system_prompt: Optional[str] = None):
        """Ask the LLM to propose edges (by node index) + strategy. Returns
        (edges, model_used, parse_error, status, reason)."""
        gw = self._get_gateway()
        node_list = [{"index": n["index"], "title": n["title"], "risk_level": n["risk_level"]}
                     for n in nodes]
        messages = [
            {"role": "system", "content": self._combine_system(_TASK_GRAPH_SYSTEM_PROMPT, system_prompt)},
            {"role": "user", "content": (
                f"任务节点（按 index）：{json.dumps(node_list, ensure_ascii=False)}\n"
                f"用户目标/约束：{user_goal or '（未提供）'}\n"
                "请输出 edges 数组，每条边 {source_index, target_index, edge_type} "
                f"（edge_type ∈ {EDGE_TYPES}）。仅表达任务依赖顺序，不得越过 Gate。")},
        ]
        result = await gw.call(messages=messages, strategy_id=strategy_id,
                               max_tokens=2048, temperature=0.2, source="api")
        if result.get("status") != "completed":
            reason = result.get("error_message") or result.get("error_category") or "model_call_failed"
            return [], result.get("model"), False, "failed", str(reason)
        parsed, parse_error = self._parse(result.get("content", ""))
        edges = parsed.get("edges", []) if isinstance(parsed.get("edges"), list) else []
        return edges, result.get("model"), parse_error, "completed", ""

    def _build_validated_edges(self, nodes: list, proposed: list):
        """Turn LLM index-edges into strategy edges, inject high-risk gate safety,
        run T5 validate_edges. On invalid/empty → degrade to single-chain sequence
        (guaranteed valid). Returns (normalized_edges, degraded, validation_errors)."""
        from app.services.task_graph_service import validate_edges, _HIGH_RISK

        n = len(nodes)
        risk_by_index = {i: nodes[i]["risk_level"] for i in range(n)}

        def _edge(si: int, ti: int, etype: str) -> dict:
            risk = self._max_risk([risk_by_index.get(si, "L0"), risk_by_index.get(ti, "L0")])
            e = {"edge_id": f"e-{nodes[si]['node_id']}-{nodes[ti]['node_id']}",
                 "source_node_id": nodes[si]["node_id"],
                 "target_node_id": nodes[ti]["node_id"],
                 "edge_type": etype if etype in EDGE_TYPES else "sequence",
                 "risk_level": risk}
            if risk in _HIGH_RISK:   # high-risk edge must Gate (红线; keeps validation green)
                e["gate_policy"] = {"gate_required": True, "risk_level": risk}
            return e

        candidate: list[dict] = []
        for spec in proposed:
            if not isinstance(spec, dict):
                continue
            si, ti = spec.get("source_index"), spec.get("target_index")
            if isinstance(si, int) and isinstance(ti, int) and 0 <= si < n and 0 <= ti < n and si != ti:
                candidate.append(_edge(si, ti, spec.get("edge_type", "sequence")))

        if candidate:
            gv = validate_edges(candidate, normalize=True)
            if gv.valid:
                return [e.normalized for e in gv.edges], False, []
            val_errors = gv.errors
        else:
            val_errors = ["模型未提议有效边"] if n > 1 else []

        # degrade → single-chain sequence (最简退化, guaranteed valid)
        chain = [_edge(i, i + 1, "sequence") for i in range(n - 1)]
        gv = validate_edges(chain, normalize=True)
        return [e.normalized for e in gv.edges], (n > 1), val_errors

    def _persist_task_graph(self, project_id: str, run_id: Optional[str], stage: str,
                            stage_plan_id: str, nodes: list, edges: list,
                            model_used: Optional[str]) -> str:
        from app.models.task_graph import TaskGraph, TaskNode

        db = self._db()
        try:
            tg = TaskGraph(
                project_id=project_id, run_id=run_id or None,
                stage=(stage or "p3").lower(), stage_plan_ref=stage_plan_id,
                task_plan_refs=[nd["task_plan_ref"] for nd in nodes],
                title="P3 TaskGraph", graph_status="draft",
                edges=edges, version=1,
            )
            db.add(tg)
            db.flush()
            for nd in nodes:
                db.add(TaskNode(
                    node_id=nd["node_id"], task_graph_id=tg.task_graph_id,
                    project_id=project_id, run_id=run_id or None,
                    stage=(stage or "p3").lower(), node_type="execution",
                    title=nd["title"], input_refs=nd["input_refs"],
                    resource_refs=nd["required_resources"],
                    model_policy_override=nd["model_policy_override"],
                    permission_boundary=nd["permission_boundary"],
                    risk_level=nd["risk_level"],
                ))
            db.commit()
            return tg.task_graph_id
        finally:
            db.close()

    # ── T16: P3 Evidence (§5.7 six items) ────────────────────────────────────
    def persist_p3_evidence(self, project_id: str, sp_res, batch_res, tg_res,
                            *, stage: str = "p3", aet_service=None,
                            context_refs: Optional[list] = None,
                            skill_refs: Optional[list] = None) -> list:
        """Persist the §5.7 six P3 Evidence items to workspace/evidence/ (D-066).

        §5.10 honest failure: Evidence is written only when the stage plan, task
        batch AND task graph all completed — an incomplete plan never fakes
        completed Evidence. Returns the persisted evidence dicts (Gate refs).
        """
        if not (sp_res and sp_res.status == "completed"
                and batch_res and batch_res.status == "completed"
                and tg_res and tg_res.status == "completed"):
            return []
        aet = aet_service or self._get_aet()
        evidence = self.build_p3_evidence(project_id, sp_res, batch_res, tg_res,
                                          context_refs=context_refs, skill_refs=skill_refs)
        persisted: list = []
        for ev in evidence:
            extra = {k: v for k, v in ev.items() if k not in ("evidence_id", "type", "claim")}
            try:
                w = aet.write_evidence(
                    project_id=project_id, evidence_id=ev["evidence_id"],
                    evidence_type=ev["type"], status="candidate",
                    source="p3_planning", claim=ev.get("claim", ""), stage=stage, extra=extra)
                persisted.append(w)
            except Exception:
                logger.warning("P3 Evidence persist failed for %s", ev["evidence_id"], exc_info=True)
        return persisted

    def build_p3_evidence(self, project_id: str, sp_res, batch_res, tg_res,
                          *, context_refs: Optional[list] = None,
                          skill_refs: Optional[list] = None) -> list:
        """Build the §5.7 six Evidence items, each traceable to the generated
        Stage Plan / Task Plans / TaskGraph (read back from DB for real refs)."""
        from app.models.stage_plan import StagePlan, TaskPlan
        from app.models.task_graph import TaskGraph

        db = self._db()
        try:
            sp = db.get(StagePlan, sp_res.stage_plan_id) if sp_res.stage_plan_id else None
            detail = (sp.plan_detail or {}) if sp else {}
            sp_scope = (sp.scope or {}) if sp else {}
            tps = (db.query(TaskPlan)
                   .filter(TaskPlan.stage_plan_ref == sp_res.stage_plan_id).all()) if sp else []
            tg = db.get(TaskGraph, tg_res.task_graph_id) if tg_res.task_graph_id else None
            edges = (tg.edges or []) if tg else []
        finally:
            db.close()

        high_risk = [{"task_plan_id": t.task_plan_id, "title": t.title, "risk_level": t.risk_level}
                     for t in tps if t.risk_level in ("L4", "L5")]
        per_task_validation = [{"task_plan_id": t.task_plan_id,
                                "validation_method": t.validation_method} for t in tps]
        uncertainties = self._read_p2_uncertainties(project_id)

        return [
            {"evidence_id": "ev-p3-plan-from-p2", "type": "plan_provenance",
             "claim": "方案来源于 P2 评估", "stage_plan_ref": sp_res.stage_plan_id,
             "p2_sources": detail.get("p2_sources", []),
             "context_refs": context_refs or [], "skill_refs": skill_refs or []},   # §5.7-1 + P1-C provenance
            {"evidence_id": "ev-p3-task-coverage", "type": "task_coverage",
             "claim": "Task Plan 覆盖目标范围", "scope": sp_scope,
             "task_plan_refs": [t.task_plan_id for t in tps], "count": len(tps)},  # §5.7-2
            {"evidence_id": "ev-p3-edge-strategy", "type": "edge_strategy_explicit",
             "claim": "TaskGraph 边策略显式", "task_graph_ref": tg_res.task_graph_id,
             "edge_count": len(edges), "degraded": tg_res.degraded,
             "validated": True},                                               # §5.7-3
            {"evidence_id": "ev-p3-high-risk", "type": "high_risk_identified",
             "claim": "高风险动作已标识", "items": high_risk,
             "batch_gate_required": bool(getattr(batch_res, "gate_required", False))},  # §5.7-4
            {"evidence_id": "ev-p3-validation", "type": "validation_in_plan",
             "claim": "P5 验证方法已纳入计划",
             "validation_strategy": detail.get("validation_strategy", ""),
             "per_task": per_task_validation},                                 # §5.7-5
            {"evidence_id": "ev-p3-open-uncertainties", "type": "open_uncertainties",
             "claim": "未解决不确定项已登记", "items": uncertainties},           # §5.7-6
        ]

    def _read_p2_uncertainties(self, project_id: str) -> list:
        """Carry forward open uncertainties from the P2 assessment report (§5.7-6).
        Best-effort; missing artifact → empty (recorded honestly, not invented)."""
        try:
            from app.services.workspace_service import workspace_path
            fp = workspace_path(project_id) / "artifacts" / "p2_assessment_report.json"
            if fp.exists():
                data = json.loads(fp.read_text(encoding="utf-8"))
                return data.get("uncertainty_list", []) or []
        except Exception:
            logger.debug("read P2 uncertainties failed (advisory)", exc_info=True)
        return []

    # ── inputs (§5.3 / §5.7-1: plan derives from P2 assessment) ──────────────
    def _gather_p2_inputs(self, project_id: str, user_goal: str) -> dict:
        inputs: dict[str, Any] = {"project_id": project_id, "user_goal": user_goal,
                                  "sources_read": [], "missing": []}
        try:
            from app.services.workspace_service import workspace_path
            art = workspace_path(project_id) / "artifacts"
            for name in ("p2_assessment_report.json", "p2_risk_list.json",
                         "p2_blocker_list.json", "p2_validation_gaps.json",
                         "p2_resource_needs.json"):
                fp = art / name
                if fp.exists():
                    try:
                        inputs[name] = json.loads(fp.read_text(encoding="utf-8"))
                        inputs["sources_read"].append(name)
                    except Exception:
                        inputs["missing"].append(f"{name}(unreadable)")
                else:
                    inputs["missing"].append(name)
        except Exception as e:
            inputs["missing"].append(f"workspace_error: {e}")
        return inputs

    def _build_user_prompt(self, inputs: dict) -> str:
        return (
            f"项目 ID：{inputs.get('project_id')}\n"
            f"用户目标/范围：{inputs.get('user_goal') or '（未提供）'}\n"
            f"已读取 P2 评估输入：{inputs.get('sources_read')}\n"
            f"缺失输入（登记为不确定项来源，不得脑补）：{inputs.get('missing')}\n"
            f"P2 评估摘要：{json.dumps({k: v for k, v in inputs.items() if k.endswith('.json')}, ensure_ascii=False)[:3500]}\n"
            "请据此生成 Stage Plan（JSON）。"
        )

    # ── parse ────────────────────────────────────────────────────────────────
    def _parse(self, content: str) -> tuple[dict, bool]:
        text = (content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data, False
        except Exception:
            pass
        # unparseable → honest: keep raw, mark parse_error (NodeLoop ReviewPass retries)
        return {"objective": "", "raw": text[:2000]}, True

    # ── persist (real StagePlan row, draft pending Gate) ─────────────────────
    def _persist_stage_plan(self, project_id: str, run_id: Optional[str], stage: str,
                            parsed: dict, model_used: Optional[str], inputs: dict) -> str:
        from app.models.stage_plan import StagePlan
        db = self._db()
        try:
            risk = parsed.get("risk_level", "L0")
            sp = StagePlan(
                project_id=project_id,
                run_id=run_id or None,
                stage=(stage or "p3").lower(),
                plan_status="draft",
                objective=parsed.get("objective") or None,
                scope={"scope": parsed.get("scope", []),
                       "out_of_scope": parsed.get("out_of_scope", [])},
                risk_level=risk if risk in RISK_LEVELS else "L0",
                permission_boundary=parsed.get("permission_boundary") or None,
                expected_artifacts=parsed.get("expected_artifacts", []),
                expected_evidence=parsed.get("expected_evidence", []),
                gate_policy=parsed.get("gate_policy", {}),
                plan_summary=(parsed.get("objective") or "")[:500] or None,
                plan_detail={
                    "completion_criteria": parsed.get("completion_criteria", []),
                    "validation_strategy": parsed.get("validation_strategy", ""),
                    "source": "p3_planning", "model_used": model_used,
                    "p2_sources": inputs.get("sources_read", []),
                    "parse_error": bool(parsed.get("raw")) and not parsed.get("objective"),
                },
                created_by="p3_planning_agent",
            )
            db.add(sp)
            db.commit()
            db.refresh(sp)
            return sp.stage_plan_id
        finally:
            db.close()

    def _trace(self, summary: str, project_id, run_id, stage) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.write("model_call", action="p3_planning", summary=summary,
                              project_id=project_id, run_id=run_id, stage=stage)
        except Exception:
            logger.debug("trace write failed (advisory)", exc_info=True)
