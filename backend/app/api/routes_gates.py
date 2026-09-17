"""Gate, Policy Check, and Risk Assessment API routes."""

import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services

logger = logging.getLogger("rebuild.routes_gates")
from app.schemas.gate import (
    GateDecisionRequest,
    PolicyCheckRequest, RiskAssessmentRequest,
    GateListResponse,
)
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects/{project_id}", tags=["gates"])


def _svc():
    return get_services()


@router.get("/gates")
async def list_gates(project_id: str):
    svc = _svc()
    gates = svc.gate_service.list_by_project(project_id)
    svc.trace_writer.write("gate_event", action="list_gates",
                           summary=f"Listed {len(gates)} gates", project_id=project_id)
    return SuccessEnvelope(
        data=GateListResponse(gates=gates, total=len(gates)),
        meta=Meta(),
    )


@router.get("/gates/active")
async def get_active_gate(project_id: str):
    svc = _svc()
    gate = svc.gate_service.get_active(project_id)
    svc.trace_writer.write("gate_event", action="get_active_gate",
                           summary=f"Active gate: {gate.gate_id if gate else 'none'}",
                           project_id=project_id,
                           run_id=(gate.run_id if gate else None))
    return SuccessEnvelope(data=gate, meta=Meta())


@router.post("/gates")
async def create_gate(project_id: str, gate_data: dict):
    svc = _svc()
    gate = svc.gate_service.create(
        project_id=project_id,
        run_id=gate_data.get("run_id", ""),
        stage=gate_data.get("stage", ""),
        gate_type=gate_data.get("gate_type", "manual_confirmation"),
        reason=gate_data.get("reason", ""),
        risk_level=gate_data.get("risk_level", "L0"),
        summary=gate_data.get("summary", ""),
        options=gate_data.get("options"),
    )
    svc.trace_writer.write("gate_event", action="create_gate",
                           summary=f"Created gate {gate.gate_id}", project_id=project_id,
                           run_id=(gate.run_id or None))
    return SuccessEnvelope(data=gate, meta=Meta())


@router.post("/gates/{gate_id}/decision")
async def decide_gate(project_id: str, gate_id: str, req: GateDecisionRequest):
    """Resolve a Gate decision. WP-6: when a LangGraph checkpoint thread exists for
    gate.run_id, drives FlowRuntime.resume (graph advances stage); GateService records
    with drive_promotion=False to avoid double-advancement. Non-graph gates advance
    directly (drive_promotion=True).

    幂等契约（D-01）：只有 gate_status == "waiting_decision" 的 Gate 可被本路由决策。
    对已决策 Gate 重复提交同一决策 → 200 无操作（不驱动图、不写审计）；提交不同决策
    → 409（不静默改判）。详见下方守卫处的完整说明。"""
    from app.graph.runtime import graph_pending_gate_ids
    from app.api.routes_stages import _ensure_graph_task, _run_graph_bg
    # B-ACC-PROMOTION-DECISION-NOGUARD 解除条件③：`_stage_has_real_artifact` 已提取到
    # gate_service（用户裁决 Q-B），本路由与 routes_stages.decide_promotion 共用同一份判据。
    # 本处【只改 import】，下方调用点语义与文案未动。
    from app.services.gate_service import VALID_DECISIONS, _stage_has_real_artifact

    svc = _svc()
    # Look up gate first to get run_id (needed for graph thread check)
    gate = svc.gate_service.get(gate_id)
    if gate is None:
        raise HTTPException(404, f"Gate {gate_id} not found")

    run_id = gate.run_id or ""
    graph_driven = False

    # D-01 / B-R22-GATE-REDECIDE-REDRIVE：已决策 Gate 再收到决策提交时的幂等拦截。
    #
    # 【为何拦】下面的图驱动分支（R17-6）只判「图线程是否仍暂停」，不判 Gate 是否已被决策。
    #   一个已 approved 的 Gate 再收到一次 approve，就会再次 Command(resume=...) 恢复同一
    #   checkpoint 线程 → 整个阶段被重新执行一遍。V26.2 总验收真实规模真跑实测：对同一个
    #   已批准的 p1 plan_presentation Gate 重复提交 5 次 → 产生 5 个重复的 p1 晋级 Gate、
    #   阶段产物分两批 mtime 互相覆写（同阶段两次执行结果还不一致）、真实 LLM 额度被重复消耗。
    #   GateService.decide() 的幂等守卫（gate_service.py:166）位于图驱动【之后】，保护不到这里。
    #
    # 【拦哪一类】只拦「HTTP 客户端对已离开 waiting_decision 的 Gate 再次提交决策」——
    #   即重试客户端、用户双击、轮询式驱动（真跑中的跟踪脚本正属此类）。
    #   判据取 gate_status 而非"调用来源"或 decision 字段：waiting_decision 是本平台既有的
    #   「可决策」定义（GateService.get_active 以它筛 active gate；StageService.promote 只挑
    #   waiting_decision 的 Gate，否则报"无待决 Gate 可决策"），本守卫与之对齐即语义统一；
    #   它还额外覆盖 mark_consumed 写入的 "consumed"（一次性高风险授权已被消费的 Gate，
    #   不得靠再提交一次决策把 gate_status 改回 approved 而复活授权）。
    #
    # 【为何不拦另一类】图节点在 resume 时「重新应用同一决策」走的是
    #   app/graph/nodes.py:502 → RealGateBackend.decide()（app/graph/gate_backend.py:124）
    #   → GateService.decide()，**完全不经过本 HTTP 路由**，故本守卫对"图内部重放"没有任何
    #   影响；那条既有幂等场景仍由 gate_service.py:166 的守卫处理（保持不变）。
    #   正常【首次】批准（gate_status == "waiting_decision"）也不受影响，照旧走下面的图恢复路径。
    #
    # 【为何置于产物校验之前】本请求是对既有决策的重放，不改任何状态、不驱动图、不晋级，
    #   因此无须（也不该）再跑一次晋级产物校验；R17-2 的空壳晋级校验只在真正会改状态的
    #   路径上起作用，此处早返回不构成对它的绕过。
    if gate.gate_status != "waiting_decision":
        submitted = (req.decision or "").strip().lower()
        if submitted not in VALID_DECISIONS:
            raise HTTPException(
                400, f"非法 Gate 决策：{req.decision!r}（允许 {sorted(VALID_DECISIONS)}）")
        if submitted != (gate.decision or ""):
            # 发声（公理 3）：不静默改写一个已生效的决策，也不假装新决策已被受理。
            # 需要返工请对新开的 Gate 决策，而不是改判旧 Gate。
            raise HTTPException(
                409,
                f"Gate {gate_id} 已被决策为 {gate.decision or '(空)'!r}"
                f"（当前状态 {gate.gate_status}，决策时间 {gate.decided_at or '未记录'}），"
                f"不接受改判为 {submitted!r}。如需返工请对新的 Gate 提交决策。",
            )
        logger.info("Gate %s 重复提交同一决策 %s（当前状态 %s）：幂等无操作，不驱动图",
                    gate_id, submitted, gate.gate_status)
        svc.trace_writer.write("gate_event", action="decide_gate_noop",
                               summary=f"Gate {gate_id} already decided ({gate.decision}); "
                                       f"repeat submit ignored (idempotent, graph not re-driven)",
                               project_id=project_id, run_id=(run_id or None))
        return SuccessEnvelope(
            data={"gate": gate.model_dump(), "audit": None, "graph_driven": False,
                  "transition_mode": "noop_already_decided"},
            meta=Meta(source_status="real"),
        )

    # R17-2 V-R17-1B-2/P1: gate 晋级强绑阶段产物。对 stage_promotion gate，晋级前校验
    # 该 run+stage 有真实产物（task_graph 存在 或 artifact_refs 非空），杜绝空壳晋级。
    # 置于 decide() 之前，确保 graph 路径和 direct 路径都强制校验。
    if gate.gate_type == "stage_promotion" and run_id:
        if not _stage_has_real_artifact(svc, run_id, gate.stage or "", gate.artifact_refs):
            raise HTTPException(
                422,
                f"阶段 {gate.stage} 晋级被拒绝：run {run_id} 在该阶段无真实产物"
                f"（task_graph 未创建且 artifact_refs 为空）。请先完成阶段执行再申请晋级。"
            )

    # R17-6 + B-ACC-DECISION-CROSSGATE-INJECTION：只有「图当前正暂停在【本】Gate 上」时，
    # 才可以把本次决策注入 graph resume。
    #
    # 【原缺陷】旧条件是 `if (gate.checkpoint_ref or run_id) and run_id: if await
    #   graph_thread_active(run_id)`——两个判据都是 run 级的：checkpoint_ref 对同 run 的
    #   每个图创建 Gate 恒等于 run_id（gate_backend.py: checkpoint_ref=run_id），
    #   graph_thread_active 只答"该 run 的线程停着吗"。于是对同 run 里【任意】一个
    #   waiting_decision 的 Gate 提交决策，都会把 req.decision 原样 Command(resume=) 进图，
    #   而图恢复后按 state["pending_gate"] 把它写到【图自己暂停的那个】Gate 上。
    #   真跑坐实：对 action_approval Gate gate-22343c 提交 reject，0.5 秒后同 run 的
    #   stage_promotion Gate gate-9a4201 也变成 rejected（从未对它提交过任何决策）。
    #   更严重的是本函数上方的 R17-2 空壳晋级校验按【被提交 Gate】的类型判断，提交
    #   action_approval 时整道守卫被跳过 ⇒ 可经安全 Gate 驱动一次未校验的阶段晋级。
    #
    # 【新判据】graph_pending_gate_ids(run_id) 直接读 checkpoint 快照，解析出图暂停点的
    #   gate_id（interrupt 载荷 ∩ state.pending_gate，两源一致才认，详见该函数 docstring）。
    #   gate_id 在集合里 = 被提交 Gate 就是图恢复后会被写入的那个 Gate ⇒ 决策不可能落到
    #   别的 Gate 上；同时"决策最终作用的 Gate == 被提交 Gate"，上方按被提交 Gate 做的
    #   空壳晋级校验因此校的正是真正被晋级的那个 Gate（解除条件③）。
    #
    # 【非晋级类安全 Gate】action_approval / desensitization_override 这类 Gate 由
    #   agent_loop / routes_registry / routes_toggle / opencode_acp_client 创建，**从不**是
    #   图暂停点（全图唯一的 interrupt() 在 nodes.py 的 {stage}_gate 节点，其 pending_gate
    #   只由 make_work_node 经 RealGateBackend.create 产生）⇒ 它们必然不在上述集合里，
    #   决策只走直连路径（gate_service.decide 只对 gate_type=="stage_promotion" 调
    #   _apply_promotion，故也不会晋级任何阶段）。这是解除条件②要求的效果，用同一性判据
    #   自然达成，不额外维护类型黑/白名单（图暂停点类型是开放集：stage_promotion /
    #   plan_presentation / plan_review / source_pending / model_unavailable …，名单必然漂移）。
    #
    # 【图暂停在别的 Gate 上时】不驱动图，但仍照常记录本 Gate 的决策（这是真实且合法的
    #   场景：阶段执行途中 agent 停在 L3+ 工具审批 Gate 上等人批，而图停在别处），
    #   并 warning 发声，便于事后核对状态。
    if run_id:
        try:
            paused_gate_ids = await graph_pending_gate_ids(run_id)
        except Exception as exc:
            logging.getLogger("rebuild.routes_gates").warning(
                "读取图暂停点失败 gate=%s run=%s：本次不驱动图（退回直连）: %s",
                gate_id, run_id, exc, exc_info=True)
            paused_gate_ids = frozenset()
        if gate_id in paused_gate_ids:
            try:
                _ensure_graph_task(_run_graph_bg(run_id, req.decision, project_id, gate.stage or ""))
                graph_driven = True
            except Exception as exc:
                logging.getLogger("rebuild.routes_gates").warning(
                    "graph task launch failed for gate %s run %s: %s", gate_id, run_id, exc,
                    exc_info=True)
                graph_driven = False
        elif paused_gate_ids:
            logger.warning(
                "Gate %s（type=%s，run=%s）不是图暂停点（图停在 %s）：本次决策只作用于该 "
                "Gate 自身，不驱动阶段图 resume（B-ACC-DECISION-CROSSGATE-INJECTION 守卫）",
                gate_id, gate.gate_type, run_id, sorted(paused_gate_ids))

    try:
        gate_resp, audit = svc.gate_service.decide(gate_id, req, drive_promotion=not graph_driven)
    except ValueError as e:
        # R17-2 V-R17-1B-2: 无产物晋级拒绝统一 422（非法值仍 400）
        code = 422 if "无真实产物" in str(e) else 400
        raise HTTPException(code, str(e))
    if gate_resp is None:
        raise HTTPException(404, f"Gate {gate_id} not found")

    svc.trace_writer.write("gate_event", action="decide_gate",
                           summary=f"Gate {gate_id} decision: {req.decision} "
                                   f"({'graph-driven' if graph_driven else 'direct'})",
                           project_id=project_id)
    return SuccessEnvelope(
        data={"gate": gate_resp.model_dump(), "audit": audit, "graph_driven": graph_driven,
              "transition_mode": "real_background" if graph_driven else "direct"},
        meta=Meta(source_status="real"),
    )


@router.post("/policy/check")
async def policy_check(project_id: str, req: PolicyCheckRequest):
    svc = _svc()
    result = svc.gate_service.policy_check(req)
    svc.trace_writer.write("policy_check", action="policy_check",
                           summary=f"Policy check: allowed={result.allowed}", project_id=project_id)
    return SuccessEnvelope(data=result, meta=Meta(source_status="real"))


@router.post("/risk/assess")
async def risk_assess(project_id: str, req: RiskAssessmentRequest):
    svc = _svc()
    result = svc.gate_service.risk_assess(req)
    svc.trace_writer.write("policy_check", action="risk_assess",
                           summary=f"Risk assessment: {result.risk_level}", project_id=project_id)
    return SuccessEnvelope(data=result, meta=Meta(source_status="real"))
