"""LangGraph node functions for the P0-P6 orchestration graph (R9-5-1, T3 + T9).

Design — work/gate node separation (RK: interrupt re-runs the whole node on resume):
  {stage}_work : runs the stage business via StageLoop (small loop, D-091), produces
                 the three reports (D-092), creates the promotion Gate. Runs once per
                 entry; re-runs only on request_changes (rework).
  {stage}_gate : calls interrupt() to pause for the user Gate decision; on resume,
                 applies the decision via the gate backend and emits routing state.
                 Kept minimal so resume re-execution is cheap and side-effect-safe.

Stage handlers are pluggable (register_handler): P0/P1/P2 wire to real services
(SourceMaterializer / FullStackProfiler / AssessmentService via ReviewPass); P3/P4/P5
are real/skeleton handlers (P3 planning / P4 TaskGraph execution / P5 verification
skeleton — C3 reads P4 input + creates validation plan, C5 runs real commands). P6
stays a future stub (00-总规划 §1.2, G7 诚实标记). Tests inject fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Protocol

from langgraph.types import interrupt

from app.graph.state import GraphState, next_stage
from app.graph.stage_loop import StageLoop
from app.graph.stage_retry import run_stage_with_transient_retry
from app.services.review_pass import ReviewResult

logger = logging.getLogger("rebuild.graph.nodes")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ev(stage: str, action: str, summary: str, **extra) -> dict:
    return {"stage": stage, "action": action, "summary": summary,
            "at": _now(), **extra}


# ── Stage handler registry ──────────────────────────────────────────────
class StageHandler(Protocol):
    """Real business for a stage. execute returns a result dict; review judges it."""
    goal: str
    acceptance_criteria: List[str]
    planned_actions: List[str]

    async def execute(self, state: GraphState) -> dict: ...
    def review(self, result: dict) -> ReviewResult: ...


_HANDLERS: Dict[str, StageHandler] = {}


def register_handler(stage: str, handler: StageHandler) -> None:
    _HANDLERS[stage] = handler


def get_handler(stage: str) -> Optional[StageHandler]:
    return _HANDLERS.get(stage)


def clear_handlers() -> None:  # test helper
    _HANDLERS.clear()


# ── Gate backend (DB-persisted Gate create/decide) ───────────────────────
class GateBackend(Protocol):
    def create(self, *, project_id: str, run_id: str, stage: str,
               artifact_refs: List[str],
               gate_type: str = "stage_promotion",
               metadata: Optional[dict] = None) -> str: ...
    def decide(self, *, gate_id: str, decision: str) -> None: ...


_gate_backend: Optional[GateBackend] = None


def set_gate_backend(backend: Optional[GateBackend]) -> None:
    global _gate_backend
    _gate_backend = backend


# tracer/auditor are optional process singletons injected by the app/runtime
_tracer = None
_auditor = None


def set_tracer_auditor(tracer=None, auditor=None) -> None:
    global _tracer, _auditor
    _tracer = tracer
    _auditor = auditor


_DECISION_TO_STATUS = {
    "approve": "completed",
    "reject": "blocked",
    "request_changes": "changes_requested",
}


# ── R17.3-6 WP-2 全量阶段级 Agent 工作流（批 A：P1 样例；批 B：扩展 P0-P6 全阶段）──
# 启用 Agent 工作流的阶段：WorkAgent 作为 StageLoop.execute_fn、独立 ValidationAgent
# 作为 review_fn 注入（Q-WP2-2，不新增图节点，保 checkpoint/resume 不破坏，D-037）。
# 批 B：现有 RealP0-P6Handler 重定位为 WorkAgent 可调用的确定性/LLM Tool（r3 约束 2）。
_AGENT_WORKFLOW_STAGES = {"p0", "p1", "p2", "p3", "p4", "p5", "p6"}


def _agent_workflow_enabled(stage: str) -> bool:
    return stage in _AGENT_WORKFLOW_STAGES


# GAP-AGT-1: infer the action/tool types a stage plan implies, so the Auto Review Agent's
# Policy floor (D-031) can classify risk. Derived from the plan text + stage semantics
# (generic, §3.6 no hardcoded MicroOA): a step mentioning command execution → L4
# execute_command; a step mentioning writing code/patches → L3 write_file.
def _infer_plan_action_types(stage: str, handler) -> list:
    action_types: list[str] = []
    text = " ".join(str(a) for a in (getattr(handler, "planned_actions", []) or []))
    low = text.lower()
    if any(k in text for k in ("命令", "执行命令", "运行")) or any(
            k in low for k in ("command", "exec", "run_", "shell")):
        action_types.append("execute_command")
    if any(k in text for k in ("写", "补丁", "代码", "落盘")) or any(
            k in low for k in ("write", "patch", "code")):
        action_types.append("write_file")
    # P4 is the code-writing / command-running stage — always carries L3+/L4 semantics.
    if stage == "p4" and "execute_command" not in action_types:
        action_types.append("execute_command")
    return action_types


def _finalize_agent_gate_material(stage: str, project_id: str, run_id: str,
                                  work_agent, validation_agent) -> List[str]:
    """合格后：由 WorkAgent(partial) + ValidationAgent(verdict) 合成最终 Gate Brief，
    返回 Agent 侧审核材料 refs（work_plan / gate_brief / validation / claim_evidence_map）
    供挂到 promotion Gate 的 artifact_refs（D-101 真实内容）。"""
    refs: List[str] = []
    wa = getattr(work_agent, "last_result", None)
    va = getattr(validation_agent, "last_result", None)
    if wa is None:
        return refs
    partial = dict(wa.gate_brief_partial or {})
    verdict = None
    cem_summary = {"total": 0}
    if va is not None:
        verdict = {"verdict": va.verdict, "passed": va.passed,
                   "issues_count": len(va.issues), "agent_id": va.agent_id}
        cem_summary = va.claim_evidence_verification or cem_summary
    try:
        from app.graph.stage_reports import StageReports
        reports = StageReports(project_id, stage)
        gb_ref = reports.gate_brief(
            stage=stage,
            what_happened=partial.get("what_happened", ""),
            key_artifacts=partial.get("key_artifacts", []),
            risks=partial.get("risks", []),
            honest_notes=partial.get("honest_notes", ""),
            validation_verdict=verdict,
            claim_evidence_summary=cem_summary,
        )
    except Exception:
        gb_ref = wa.gate_brief_ref
    for r in (wa.work_plan_ref, gb_ref, wa.claim_evidence_map_ref):
        if r and r not in refs:
            refs.append(r)
    if va is not None:
        # D-107: validation report lives in artifacts/{stage}/ subdirectory.
        va_ref = f"artifacts/{stage}/{stage}_validation.json"
        if va_ref not in refs:
            refs.append(va_ref)
    # GATE-01（承 R17.3-4）：P5 阶段把 p5_validation_report.json 挂到 Gate 审核材料，
    # 使用户 Gate 决策可见真实验证报告（D-101 真实内容）。
    if stage == "p5":
        try:
            from app.services.workspace_service import workspace_path
            p5_report = "artifacts/p5_validation_report.json"
            if (workspace_path(project_id) / p5_report).exists() and p5_report not in refs:
                refs.append(p5_report)
        except Exception:
            logger.debug("GATE-01: 挂 p5_validation_report 失败（advisory）", exc_info=True)
    return refs


# ── Node factories ────────────────────────────────────────────────────────
def make_work_node(stage: str) -> Callable[[GraphState], Awaitable[dict]]:
    async def work(state: GraphState) -> dict:
        project_id = state.get("project_id", "")
        run_id = state.get("run_id", "")
        handler = get_handler(stage)

        if handler is None:
            # P7+ stub: structurally walkable, no business (future, G7 honest)
            return {
                "current_stage": stage,
                "stage_status": {stage: "future"},
                "events": [_ev(stage, "future",
                               f"{stage} node is a future stub (business in later R-series)")],
            }

        # R17-X 两阶段流程（已删除 per-stage plan_review 欢迎门，B-R17X-PLANREVIEW-1）：
        #   阶段 1（plan_presentation）: plan_only 生成接入计划 → 用户/Agent 审核（D-025）
        #   阶段 2（stage_promotion）: 执行完成 → 审核结果后晋级（D-023）
        # Auto 模式跳过阶段 1，直接执行。
        #（"欢迎 + 启动"改由前端一次性初始态页承担，不再逐阶段弹欢迎门。）
        mode = (state.get("execution_mode") or "").lower()
        plan_approved = state.get("plan_approved") or False

        if mode in ("manual", "plan") and not plan_approved:
            # 阶段 1: plan_only 生成计划 → 创建 plan_presentation gate → 等待审核
            loop = StageLoop(project_id, stage, tracer=_tracer, auditor=_auditor,
                             max_rounds=2, run_id=run_id)
            plan_res = await loop.run(
                goal=getattr(handler, "goal", f"{stage} stage"),
                acceptance_criteria=getattr(handler, "acceptance_criteria", []),
                planned_actions=getattr(handler, "planned_actions", []),
                execute_fn=lambda: handler.execute(state),
                review_fn=handler.review,
                plan_only=True,
            )
            # WP-2 批 A：Agent 工作流阶段（P1）由 WorkAgent 确定性合成动态工作计划
            # （Q-WP2-4：基于真实项目事实，非 handler 静态类属性），挂 plan_presentation。
            extra_plan_refs: List[str] = []
            if _agent_workflow_enabled(stage):
                try:
                    from app.services.work_agent import WorkAgent
                    _wa = WorkAgent(stage, project_id, run_id,
                                    tracer=_tracer, auditor=_auditor, handler=handler)
                    extra_plan_refs.append(_wa.build_work_plan(state))
                except Exception:
                    logger.warning("WP-2 %s plan_only WorkAgent 动态计划合成失败（advisory）",
                                   stage, exc_info=True)
            plan_refs = list(plan_res.report_refs) + extra_plan_refs
            plan_gate_id = ""
            if _gate_backend is not None:
                plan_gate_id = _gate_backend.create(
                    project_id=project_id, run_id=run_id, stage=stage,
                    artifact_refs=plan_refs,
                    gate_type="plan_presentation",
                    metadata={"mode": mode})
            return {
                "current_stage": stage,
                "stage_status": {stage: "waiting_plan_presentation"},
                "plan_approved": True,
                "gates": {plan_gate_id: {"stage": stage, "gate_status": "waiting_decision",
                                          "gate_type": "plan_presentation"}} if plan_gate_id else {},
                "pending_gate": {"gate_id": plan_gate_id, "stage": stage,
                                 "gate_type": "plan_presentation"} if plan_gate_id else None,
                "events": [_ev(stage, "plan_presentation_created",
                               f"{stage} 接入计划已生成，等待审核")],
            }

        # Auto 模式（GAP-AGT-1 / D-025 / D-028）：由 Auto Review Agent 真实审 Stage Plan
        # （LLM 判风险/越界/低置信）。判定需升级 → 创建 plan_presentation Gate 交用户确认；
        # 否则 Agent 自动放行（plan_approved=True）继续执行。Policy 为强制底线（D-031）：
        # 计划含 L4+ 动作时 Auto Review Agent 强制升级，LLM 不得绕过。
        if mode == "auto" and not plan_approved and _agent_workflow_enabled(stage):
            try:
                from app.services.auto_review_agent import AutoReviewAgent
                _planned = list(getattr(handler, "planned_actions", []) or [])
                _action_types = _infer_plan_action_types(stage, handler)
                _ara = AutoReviewAgent(stage, project_id, run_id,
                                       tracer=_tracer, auditor=_auditor)
                _review = _ara.review_plan(
                    _planned, goal=getattr(handler, "goal", f"{stage} stage"),
                    action_types=_action_types)
            except Exception:
                logger.warning("Auto Review Agent 审核异常 stage=%s（保守升级用户 Gate）",
                               stage, exc_info=True)
                _review = None
            if _review is not None and _review.needs_user_gate:
                # 升级：生成计划并挂 plan_presentation Gate（与 manual/plan 同路径）。
                loop = StageLoop(project_id, stage, tracer=_tracer, auditor=_auditor,
                                 max_rounds=2, run_id=run_id)
                plan_res = await loop.run(
                    goal=getattr(handler, "goal", f"{stage} stage"),
                    acceptance_criteria=getattr(handler, "acceptance_criteria", []),
                    planned_actions=getattr(handler, "planned_actions", []),
                    execute_fn=lambda: handler.execute(state),
                    review_fn=handler.review, plan_only=True)
                plan_gate_id = ""
                if _gate_backend is not None:
                    plan_gate_id = _gate_backend.create(
                        project_id=project_id, run_id=run_id, stage=stage,
                        artifact_refs=list(plan_res.report_refs),
                        gate_type="plan_presentation",
                        metadata={"mode": mode, "auto_review": _review.to_dict()})
                return {
                    "current_stage": stage,
                    "stage_status": {stage: "waiting_plan_presentation"},
                    "plan_approved": True,
                    "gates": {plan_gate_id: {"stage": stage, "gate_status": "waiting_decision",
                                              "gate_type": "plan_presentation"}} if plan_gate_id else {},
                    "pending_gate": {"gate_id": plan_gate_id, "stage": stage,
                                     "gate_type": "plan_presentation"} if plan_gate_id else None,
                    "events": [_ev(stage, "auto_review_escalated", _review.reason)],
                }
            # 未升级 → Auto Review Agent 放行，继续执行完整 StageLoop。

        # 阶段 3（或 Auto 模式直接）: 执行完整 StageLoop
        loop = StageLoop(project_id, stage, tracer=_tracer, auditor=_auditor,
                         max_rounds=2, run_id=run_id)
        # WP-2 批 A：Agent 工作流阶段（P1）—— WorkAgent 作为 execute_fn、独立
        # ValidationAgent 作为 review_fn 注入（Q-WP2-2/Q-WP2-5，不新增图节点）。
        # ValidationAgent 不合格 → passed=False → StageLoop/ReviewPass 触发 rework 轮
        # → WorkAgent 携反馈重跑（request_changes 打回 WorkAgent 重验），合格才建用户 Gate。
        _work_agent = None
        _validation_agent = None
        if _agent_workflow_enabled(stage):
            from app.services.work_agent import WorkAgent
            from app.services.validation_agent import ValidationAgent
            _work_agent = WorkAgent(stage, project_id, run_id,
                                    tracer=_tracer, auditor=_auditor, handler=handler)
            _validation_agent = ValidationAgent(stage, project_id, run_id,
                                                tracer=_tracer, auditor=_auditor,
                                                handler=handler)

            def _agent_review(work_result: dict) -> ReviewResult:
                rr = _validation_agent.validate(work_result)
                if not rr.passed:
                    _work_agent.set_rework_feedback(
                        {"issues": rr.issues, "recommendations": rr.recommendations})
                return rr

            res = await loop.run(
                goal=getattr(handler, "goal", f"{stage} stage"),
                acceptance_criteria=getattr(handler, "acceptance_criteria", []),
                planned_actions=getattr(handler, "planned_actions", []),
                execute_fn=lambda: run_stage_with_transient_retry(
                    stage=stage,
                    execute_fn=lambda: _work_agent.execute(state),
                    tracer=_tracer, auditor=_auditor,
                    project_id=project_id, run_id=run_id),
                review_fn=_agent_review,
            )
        else:
            res = await loop.run(
                goal=getattr(handler, "goal", f"{stage} stage"),
                acceptance_criteria=getattr(handler, "acceptance_criteria", []),
                planned_actions=getattr(handler, "planned_actions", []),
                execute_fn=lambda: run_stage_with_transient_retry(
                    stage=stage,
                    execute_fn=lambda: handler.execute(state),
                    tracer=_tracer, auditor=_auditor,
                    project_id=project_id, run_id=run_id),
                review_fn=handler.review,
            )

        if not res.passed:
            # escalated / failed → block on a Gate too, but mark escalation honestly
            # WP-1: git/empty-source failure → source_pending gate type with retry_action
            escalation_reason = res.escalation_reason or f"{stage} loop did not pass"
            _gate_type = "stage_promotion"
            _metadata: Optional[dict] = None
            # Check rounds for empty_source issue (StageLoopResult.rounds contains issue lists)
            _has_empty_source = "empty_source" in escalation_reason
            if not _has_empty_source:
                for _rnd in (res.rounds or []):
                    for _iss in (_rnd.get("issues") or []):
                        if isinstance(_iss, dict) and _iss.get("type") == "empty_source":
                            _has_empty_source = True
                            break
                    if _has_empty_source:
                        break
            if _has_empty_source:
                _gate_type = "source_pending"
                _metadata = {
                    "retry_action": "update_source_config",
                    "retry_hint": "请补充源码凭据或切换为手动导入后重新执行",
                }

            # WP-6 (Q-R17.3-6-2): 模型全失败强制中断 → 独立 model_unavailable 中断 Gate。
            # 从 rounds 的结构化 issue(type=model_unavailable) 提取已尝试模型链路 + 失败原因 +
            # 用户可采取操作，作为 Gate metadata 供前端显式报错；stage_status 置 blocked（明确失败态，
            # 非静默降级）。detect 与 empty_source 并列，模型中断优先（更明确的失败类别）。
            _model_detail: Optional[dict] = None
            for _rnd in (res.rounds or []):
                for _iss in (_rnd.get("issues") or []):
                    if isinstance(_iss, dict) and _iss.get("type") == "model_unavailable":
                        _md = _iss.get("detail")
                        _model_detail = _md if isinstance(_md, dict) else {}
                        break
                if _model_detail is not None:
                    break
            _is_model_unavailable = _model_detail is not None
            if _is_model_unavailable:
                _gate_type = "model_unavailable"
                _metadata = {
                    "interrupted_stage": _model_detail.get("interrupted_stage", stage),
                    "failure_reason": _model_detail.get("failure_reason", escalation_reason),
                    "error_category": _model_detail.get("error_category", "model_unavailable"),
                    "attempted_chain": _model_detail.get("attempted_chain", []),
                    "user_actions": _model_detail.get("user_actions", []),
                    "retry_action": "configure_model",
                    "retry_hint": "所有可用模型均不可用：请配置有效模型/API Key 或切换模型策略后重试",
                }
                if _auditor is not None:
                    try:
                        _auditor.write(
                            audit_type="model_unavailable", action="stage_interrupt_gate",
                            decision="blocked", risk_level="L2",
                            project_id=project_id, run_id=run_id, stage=stage,
                            reason=(f"{stage} 因模型全失败强制中断（{_metadata['error_category']}）；"
                                    f"已尝试 {len(_metadata['attempted_chain'])} 个模型，无静默降级/假成功"))
                    except Exception:
                        logger.debug("model_unavailable 中断 Audit 写入失败（advisory）", exc_info=True)

            gate_id = ""
            if _gate_backend is not None:
                gate_id = _gate_backend.create(
                    project_id=project_id, run_id=run_id, stage=stage,
                    artifact_refs=res.report_refs,
                    gate_type=_gate_type,
                    metadata=_metadata)
            # 模型全失败 → stage_status=blocked（明确失败态）；其余升级仍为 waiting_gate。
            _stage_state = "blocked" if _is_model_unavailable else "waiting_gate"
            _extra_pending = {}
            if _has_empty_source:
                _extra_pending = {"retry_action": "update_source_config"}
            elif _is_model_unavailable:
                _extra_pending = {"retry_action": "configure_model",
                                  "error_category": _metadata["error_category"],
                                  "attempted_chain": _metadata["attempted_chain"]}
            return {
                "current_stage": stage,
                "stage_status": {stage: _stage_state},
                "gates": {gate_id: {"stage": stage, "gate_status": "waiting_decision",
                                    "escalated": True,
                                    "gate_type": _gate_type}} if gate_id else {},
                "pending_gate": {"gate_id": gate_id, "stage": stage, "escalated": True,
                                 "gate_type": _gate_type, **_extra_pending},
                "events": [_ev(stage, "escalated_to_gate", escalation_reason,
                               gate_type=_gate_type, **_extra_pending)],
            }

        # passed → create promotion Gate with the three reports + domain artifacts, then interrupt
        result = res.result if isinstance(res.result, dict) else {}
        domain_artifacts = [str(a) for a in (result.get("artifacts") or [])]
        gate_refs = list(res.report_refs) + domain_artifacts
        # WP-2 批 A：合格后合成最终 Gate Brief（含 ValidationAgent verdict + claim-evidence
        # summary），挂 Agent 侧审核材料 refs（work_plan/gate_brief/validation/claim_evidence_map）。
        if _agent_workflow_enabled(stage) and _work_agent is not None:
            for _r in _finalize_agent_gate_material(stage, project_id, run_id,
                                                    _work_agent, _validation_agent):
                if _r not in gate_refs:
                    gate_refs.append(_r)
        gate_id = ""
        # GATE-02 (R17.3-6 WP-5): P6 handler 在 execute 内已创建唯一权威最终 Gate
        # （D-023，RealP6Handler._create_p6_final_gate，返回 p6_final_gate_id）。此处不再
        # 重复建第二个 stage_promotion Gate，而是复用该权威 Gate 并把三类报告 + Agent 侧
        # 审核材料补挂到同一 Gate——消除 P6 双 Gate。其余阶段（无 handler 建 Gate）照常建
        # promotion Gate。复用不破坏 resume：decide 路由用 run_id 触发 graph resume
        # （routes_gates.py:120 `checkpoint_ref or run_id`），且补挂后 artifact_refs 非空满足
        # 晋级产物校验（_stage_has_real_artifact）。
        _existing_final_gate = result.get("p6_final_gate_id") if stage == "p6" else None
        if _existing_final_gate and _gate_backend is not None:
            gate_id = _existing_final_gate
            try:
                _gate_backend.attach_artifact_refs(gate_id=gate_id, refs=gate_refs)
            except Exception:
                logger.warning("GATE-02: 复用 P6 最终 Gate 补挂审核材料失败（advisory）",
                               exc_info=True)
        elif _gate_backend is not None:
            gate_id = _gate_backend.create(
                project_id=project_id, run_id=run_id, stage=stage,
                artifact_refs=gate_refs)
        artifacts_inc = ([{"stage": stage, "ref": r, "kind": "stage_report"}
                          for r in res.report_refs]
                         + [{"stage": stage, "ref": r, "kind": "domain"}
                            for r in domain_artifacts])
        return {
            "current_stage": stage,
            "stage_status": {stage: "waiting_gate"},
            "gates": {gate_id: {"stage": stage, "gate_status": "waiting_decision",
                                "artifact_refs": gate_refs}} if gate_id else {},
            "pending_gate": {"gate_id": gate_id, "stage": stage},
            "artifacts": artifacts_inc,
            "events": [_ev(stage, "promotion_gate_created",
                           f"{stage} small loop passed; promotion Gate created")],
        }

    work.__name__ = f"{stage}_work"
    return work


def make_gate_node(stage: str) -> Callable[[GraphState], Awaitable[dict]]:
    async def gate(state: GraphState) -> dict:
        pg = state.get("pending_gate") or {}
        gate_id = pg.get("gate_id", "")
        gate_type = pg.get("gate_type", "stage_promotion")

        # Pause here for the user's Gate decision. On resume, `decision` is the
        # value passed via Command(resume=...). P121 HITL resume接入点 (R9-5-7).
        # R17-3: 支持 stage_promotion + plan_presentation 两种 gate 类型。
        decision = interrupt({"gate_id": gate_id, "stage": stage, "type": gate_type})
        decision = (decision or "").strip() if isinstance(decision, str) else decision

        if _gate_backend is not None and gate_id and decision in _DECISION_TO_STATUS:
            _gate_backend.decide(gate_id=gate_id, decision=decision)

        upd: dict = {
            "last_decision": decision,
            "pending_gate": None,
            "events": [_ev(stage, "gate_decided", f"{stage} promotion Gate: {decision}")],
        }
        if gate_id:
            upd["gates"] = {gate_id: {"gate_status": {
                "approve": "approved", "reject": "rejected",
                "request_changes": "changes_requested"}.get(decision, "unknown")}}

        if decision == "approve":
            # R17-3: plan_presentation 批准 → 不进阶，由 router 路由回 work 节点执行完整 StageLoop
            if gate_type == "plan_presentation":
                upd["stage_status"] = {stage: "plan_approved"}
            else:
                nxt = next_stage(stage)
                ss = {stage: "completed"}
                if nxt:
                    ss[nxt] = "in_progress"
                    upd["current_stage"] = nxt
                else:
                    upd["run_status"] = "completed"
                upd["stage_status"] = ss
                # EG-WP2A-1: 进入新阶段时重置 plan 门控。plan_approved 是「当前阶段的接入
                # 计划已通过」的标记；stage_promotion 批准晋级后必须复位，否则 make_router
                # 因 plan_approved 残留恒真而路由回本阶段 {stage}_work（形成循环 / 跳过下一
                # 阶段独立 plan_presentation gate）。复位后 manual/plan 模式下每个阶段都能
                # 到达各自的 plan_presentation gate；auto 模式该值本就为假，复位无副作用。
                upd["plan_approved"] = False
        elif decision == "reject":
            upd["stage_status"] = {stage: "blocked"}
            upd["run_status"] = "blocked"
        else:  # request_changes → rerun stage work (rework, D-091)
            upd["stage_status"] = {stage: "changes_requested"}
        return upd

    gate.__name__ = f"{stage}_gate"
    return gate


def make_router(stage: str) -> Callable[[GraphState], str]:
    """Conditional edge: route from {stage}_gate by the user's decision."""
    def route(state: GraphState) -> str:
        decision = state.get("last_decision")
        if decision == "approve":
            # R17-3: plan_presentation 批准 → 回到 work 节点执行完整 StageLoop
            #（work 节点检测到 plan_approved=True 会跳过 plan_review/plan_presentation，直接执行）
            if state.get("plan_approved"):
                return f"{stage}_work"
            nxt = next_stage(stage)
            return f"{nxt}_work" if nxt else "__end__"
        if decision == "reject":
            return "__end__"
        # request_changes (or unknown) → rework current stage
        return f"{stage}_work"
    return route
