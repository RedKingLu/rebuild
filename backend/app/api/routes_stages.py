"""Stage API routes."""

import asyncio as _asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.stage import StagePlanRequest, PromotionRequest, PromotionDecision
from app.schemas.common import SuccessEnvelope, Meta

logger = logging.getLogger("rebuild.routes_stages")

router = APIRouter(prefix="/projects/{project_id}/runs/{run_id}/stages", tags=["stages"])


def _svc():
    return get_services()


@router.get("")
async def list_stages(project_id: str, run_id: str):
    svc = _svc()
    stages = svc.stage_service.get_stages(project_id, run_id)
    svc.trace_writer.write("state_change", action="list_stages",
                           summary=f"Listed {len(stages)} stages", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data={"stages": [s.model_dump() for s in stages]}, meta=Meta())


@router.get("/{stage}")
async def get_stage(project_id: str, run_id: str, stage: str):
    svc = _svc()
    s = svc.stage_service.get_stage(project_id, run_id, stage)
    if s is None:
        raise HTTPException(404, f"Stage {stage} not found in run {run_id}")
    svc.trace_writer.write("state_change", action="get_stage",
                           summary=f"Got stage {stage}", project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=s, meta=Meta())


@router.post("/{stage}/stage-plan")
async def submit_stage_plan(project_id: str, run_id: str, stage: str, req: StagePlanRequest):
    svc = _svc()
    result = svc.stage_service.submit_stage_plan(project_id, run_id, stage, req)
    svc.trace_writer.write("state_change", action="submit_stage_plan",
                           summary=f"Submitted stage plan for {stage}", project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=result, meta=Meta())


@router.post("/{stage}/promotion-gate")
async def create_promotion_gate(project_id: str, run_id: str, stage: str, req: PromotionRequest):
    svc = _svc()
    gate = svc.gate_service.create(
        project_id=project_id, run_id=run_id, stage=stage,
        gate_type="stage_promotion", reason=f"Stage promotion to {req.target_stage}",
        summary=f"请求晋级至 {req.target_stage}", options=["approve", "reject", "request_changes"],
    )
    svc.trace_writer.write("gate_event", action="create_promotion_gate",
                           summary=f"Created promotion gate {gate.gate_id}", project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=gate, meta=Meta())


def _promotion_gate_candidates(svc, project_id: str, run_id: str, stage: str,
                               paused_gate_ids: frozenset) -> list:
    """本端点在 (run_id, stage) 上【可决策】的待决 Gate 候选集。

    候选判据两条（取并集），都不是 gate_type 名单：
      ① `gate_type == "stage_promotion"` —— 本端点的本职对象（阶段晋级）；
      ② `gate_id ∈ paused_gate_ids` —— 图此刻正暂停在它上面。**无论其 gate_type**。
         这一条是必需的：图的暂停点类型是开放集（plan_presentation / plan_review /
         source_pending / model_unavailable …，见 app/graph/nodes.py），前端 GatePanel
         对所有非安全类 Gate 都打本端点。若只认 stage_promotion，用户批准 p1 计划
         Gate 会被 409 挡死，项目卡在计划页。而"图暂停点"本身就是同一性判据的来源，
         用它做候选判据不会随新增 gate_type 漂移（新类型自动被覆盖，无名单可维护）。

    三个过滤条件（run_id / stage / waiting_decision）缺一不可：
      · run_id：跨 run 的 Gate 绝不能被本 run 的决策命中；
      · stage：URL 里的 {stage} 必须与 Gate 自己的 stage 一致 —— 这正是
        B-ACC-PROMOTION-DECISION-NOGUARD 的核心形态（图停在 p2，对 /stages/p0/
        提交 approve，旧代码照样注入图并把决策写到 p2 那个 Gate 上）；
      · waiting_decision：已决策 / 已消费的 Gate 不是可决策对象（与 GateService.get_active、
        StageService.promote 的既有"可决策"定义对齐）。
    """
    candidates = []
    for g in svc.gate_service.list_by_project(project_id):
        if g.gate_status != "waiting_decision":
            continue
        if (g.run_id or "") != run_id:
            continue
        if (g.stage or "").lower() != (stage or "").lower():
            continue
        if g.gate_type == "stage_promotion" or g.gate_id in paused_gate_ids:
            candidates.append(g)
    return candidates


def _resolve_promotion_target(svc, project_id: str, run_id: str, stage: str,
                              requested_gate_id: str | None, paused_gate_ids: frozenset):
    """求本次决策【指名】作用的那个 Gate，返回 (gate_id, gate)。判不出来就 409，绝不猜。

    B-ACC-PROMOTION-DECISION-NOGUARD 解除条件①②：本端点原先连 gate_id 字段都没有，
    结构上不可能做同一性判定；决策被原样注入图后由图写到【它自己暂停的那个】Gate 上。

    传了 gate_id → 用它，但必须通过三道对齐校验（存在 / 同 run / 同 stage / 待决），
    否则 409。**不允许**"传了一个别的 Gate 的 id 也照样执行"——那是换个入口重现同一缺陷。

    没传 gate_id → 按 (run_id, stage) 推断唯一待决对象（既有 11 个调用方走这条）：
      恰好 1 个 → 用它（行为与修复前的意图等价，向后兼容）
      0 个 / ≥2 个 → 409 且【不驱动图】。用户 2026-09-15 裁决 Q-A：「失效吧」——
        "未建 Gate 就直接驱动图"这一用法**应当失效**。0 个正是缺陷甲的形态：没有可判定
        的对象却把决策注入图，图会把它应用到自己暂停的那个 Gate 上。≥2 个真实存在过
        （B-R22-GATE-REDECIDE-REDRIVE 实测产生 5 个重复 p1 晋级 Gate），历史数据仍有
        这类记录，故必须处理而不是假设"不会发生"。
    """
    requested = (requested_gate_id or "").strip()
    if requested:
        gate = svc.gate_service.get(requested)
        if gate is None:
            raise HTTPException(404, f"Gate {requested} 不存在，无法对其提交晋级决策。")
        if (gate.run_id or "") != run_id or (gate.stage or "").lower() != (stage or "").lower():
            raise HTTPException(
                409,
                f"gate_id {requested} 与 URL 指定的对象不一致（Gate 属于 "
                f"run={gate.run_id or '(空)'} / stage={gate.stage or '(空)'}，URL 为 "
                f"run={run_id} / stage={stage}）：拒绝跨对象决策。请对该 Gate 自己的 "
                f"run/stage 路径提交，或改用 /gates/{requested}/decision。")
        if gate.gate_status != "waiting_decision":
            raise HTTPException(
                409,
                f"Gate {requested} 当前状态为 {gate.gate_status}（决策 "
                f"{gate.decision or '(空)'!r}），不是待决对象，不接受再次决策。"
                f"如需返工请对新的 Gate 提交决策。")
        return requested, gate

    candidates = _promotion_gate_candidates(svc, project_id, run_id, stage, paused_gate_ids)
    if len(candidates) == 1:
        return candidates[0].gate_id, candidates[0]
    if not candidates:
        raise HTTPException(
            409,
            f"该 run+stage 无待决晋级 Gate（run={run_id}, stage={stage}），"
            f"无法判定要决策哪个对象；请传 gate_id。"
            + (f"（图当前暂停在 {sorted(paused_gate_ids)}，"
               f"若要决策它请用它自己的 stage 路径或 /gates/{{gate_id}}/decision）"
               if paused_gate_ids else ""))
    raise HTTPException(
        409,
        f"该 run+stage 有 {len(candidates)} 个待决 Gate："
        f"{sorted(g.gate_id for g in candidates)}，无法判定要决策哪一个；请传 gate_id 指名。")


@router.post("/{stage}/promotion-decision")
async def decide_promotion(project_id: str, run_id: str, stage: str, req: PromotionDecision):
    """Resolve a stage-promotion Gate. W9: unified single decision kernel
    (GateService.decide). When the LangGraph checkpoint thread for this run is paused
    ON THE TARGET GATE, the decision drives FlowRuntime.resume (thread_id=run_id) — the
    graph node calls _gate_backend.decide() internally, so stage_service.promote() is
    SKIPPED (no double-advancement, no stale-gate fallback). Non-graph runs, and runs
    whose graph is paused somewhere else, fall back to stage_service.promote(
    drive_promotion=True) for direct advancement.

    B-ACC-PROMOTION-DECISION-NOGUARD（P0/CRITICAL）：本端点此前的图驱动判据只有 run 级的
    `graph_thread_active(run_id)`，且请求体没有 gate_id ⇒ 结构上不可能判定"决策的是哪个
    Gate"；加之图分支构造合成结果直接返回、不走 promote()，R17-2 空壳晋级校验在该分支
    **整条缺席**。现改为：先指名对象（req.gate_id 或按 run+stage 唯一推断，判不出即 409）
    → 跑 R17-2 空壳晋级校验（与 routes_gates 共用同一份判据与文案）→ 只有"图确实暂停在
    这个对象上"才注入图 resume。"""
    import uuid as _uuid
    # 【复用，不另造】同一性判据与空壳晋级判据都 import 既有实现：
    #   · graph_pending_gate_ids —— routes_gates 已在用的同一份（解除条件②明写"不另造第二份"）
    #   · _stage_has_real_artifact —— 已提取到 gate_service 的共享守卫（解除条件③，用户裁决 Q-B）
    # 在函数内 import（而非模块顶层）与 routes_gates.decide_gate 保持一致，也让测试可以
    # monkeypatch `app.graph.runtime.graph_pending_gate_ids` 同时覆盖两条路由。
    from app.graph.runtime import graph_pending_gate_ids
    from app.services.gate_service import VALID_DECISIONS, _stage_has_real_artifact

    svc = _svc()

    # P1-B: validate decision at the entry BEFORE any graph resume — an illegal
    # value must not drive the graph (which would rebuild a duplicate stage_promotion
    # Gate) nor return 200. Mirrors GateService.decide / routes_gates 400 semantics.
    decision_norm = (req.decision or "").strip().lower()
    if decision_norm not in VALID_DECISIONS:
        raise HTTPException(
            400, f"非法晋级决策：{req.decision!r}（允许 {sorted(VALID_DECISIONS)}）")

    # ── 图暂停点：同一性判定的唯一权威来源（读 checkpoint 快照，见该函数 docstring）──
    # 读失败 → 空集（fail-closed：宁可退回直连路径，也不在不知道图停在哪的情况下注入决策），
    # 与 routes_gates.decide_gate 的处理完全一致。
    try:
        paused_gate_ids = await graph_pending_gate_ids(run_id)
    except Exception as exc:
        logger.warning("读取图暂停点失败 run=%s：本次不驱动图（退回直连）: %s",
                       run_id, exc, exc_info=True)
        paused_gate_ids = frozenset()

    # ── 指名被决策的对象（判不出即 409，且不驱动图）──
    target_gate_id, target_gate = _resolve_promotion_target(
        svc, project_id, run_id, stage, req.gate_id, paused_gate_ids)

    # ── R17-2 V-R17-1B-2：空壳晋级校验，覆盖【图分支与直连分支】（解除条件③）──
    # 置于图分支【之前】，与 routes_gates.decide_gate 同构；判据函数与 422 文案逐字同源。
    # 原缺陷：图分支合成结果直接返回、不调 promote()，promote() 内的该校验整条不执行 ⇒
    # 图活跃（正常情况）时可驱动一次空壳晋级。
    if target_gate.gate_type == "stage_promotion" and run_id:
        if not _stage_has_real_artifact(svc, run_id, target_gate.stage or "",
                                        target_gate.artifact_refs):
            raise HTTPException(
                422,
                f"阶段 {target_gate.stage} 晋级被拒绝：run {run_id} 在该阶段无真实产物"
                f"（task_graph 未创建且 artifact_refs 为空）。请先完成阶段执行再申请晋级。"
            )

    # ── 同一性判定：只有"图正暂停在这个 Gate 上"才可以把决策注入 graph resume ──
    graph_driven = False
    if target_gate_id in paused_gate_ids:
        try:
            # R17-6: fire-and-forget graph resume; HTTP returns immediately.
            _ensure_graph_task(_run_graph_bg(run_id, req.decision, project_id, stage))
            graph_driven = True
        except Exception as e:
            logger.warning("graph task launch failed for run=%s: %s", run_id, e)
            graph_driven = False
    elif paused_gate_ids:
        # 真实且合法的场景：阶段执行途中图停在别处（或停在另一个 Gate 上）。照常记录本
        # Gate 的决策，但不注入图 —— 否则决策会被应用到图自己暂停的那个 Gate 上。
        logger.warning(
            "Gate %s（type=%s，run=%s，stage=%s）不是图暂停点（图停在 %s）：本次决策只作用于"
            "该 Gate 自身，不驱动阶段图 resume（B-ACC-PROMOTION-DECISION-NOGUARD 守卫）",
            target_gate_id, target_gate.gate_type, run_id, stage, sorted(paused_gate_ids))

    if graph_driven:
        # R17-6: graph resumed in the background; DB sync happens there on completion.
        # Transition is accepted (202-equivalent semantics): client should rely on SSE
        # for the next gate / stage change rather than polling this response.
        result = {
            "promotion_id": f"promo-{_uuid.uuid4().hex[:8]}",
            # 诚实回填被决策对象（旧代码在此硬编码 ""，调用方无从知道决策落到了哪个 Gate）
            "gate_id": target_gate_id,
            "from_stage": stage,
            "decision": req.decision,
            "gate_status": {
                "approve": "approved", "reject": "rejected",
                "request_changes": "changes_requested"}.get(req.decision, "unknown"),
            "audit_ref": None,
            "transition_mode": "real_background",
        }
    else:
        # Non-graph run (no active graph thread), the graph is paused on ANOTHER gate, OR
        # the graph resume launch failed → fall back to the direct single-decision path.
        # There are no graph side-effects to reconcile, so promote() decides the gate and
        # advances state.
        try:
            result = svc.stage_service.promote(
                project_id, run_id, stage, req, drive_promotion=True)
        except ValueError as e:
            # R17-2 V-R17-1B-2: 无产物晋级拒绝统一 422（非法值仍 400）
            code = 422 if "无真实产物" in str(e) else 400
            raise HTTPException(code, str(e))

    result["graph_driven"] = graph_driven

    # UX-5: on reject / request_changes, persist the reason to a rework-notes artifact so
    # the rework (Acceptance) agent knows exactly why this attempt failed. Best-effort — a
    # notes write failure must not fail the decision itself.
    if req.decision in ("reject", "request_changes") and (req.reason or "").strip():
        try:
            from app.services.workspace_service import append_rework_notes
            result["rework_notes_ref"] = append_rework_notes(
                project_id, stage, req.decision, req.reason.strip())
        except Exception as e:
            logger.warning("rework-notes write failed project=%s stage=%s: %s",
                           project_id, stage, e)

    svc.trace_writer.write("gate_event", action="decide_promotion",
                           summary=f"Promotion decision: {req.decision} "
                                   f"({'graph-driven' if graph_driven else 'direct'})",
                           project_id=project_id, run_id=run_id, stage=stage)
    return SuccessEnvelope(data=result, meta=Meta())


# ── UX-5 / UX-4 fix: TaskGraph node status for the task-overview top bar ─

@router.get("/{stage}/taskgraph")
async def get_taskgraph(project_id: str, run_id: str, stage: str):
    """Return the active TaskGraph (if any) for (project, run, stage) with per-node
    runtime status — the honest source for the UX-4 task-overview bar. Responds with an
    empty node list (no active graph) rather than fabricating tasks: the frontend falls back
    to the live task-context + real-time status when this is empty."""
    svc = _svc()
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph, TaskNode, TaskGraphRun
    from app.models.task_node_run import TaskNodeRun
    db = get_session()
    try:
        # Latest non-superseded graph run for this stage.
        graph_run = (
            db.query(TaskGraphRun)
            .filter(TaskGraphRun.run_id == run_id, TaskGraphRun.stage == stage)
            .order_by(TaskGraphRun.started_at.desc().nullslast()
                      if hasattr(TaskGraphRun, "started_at") else TaskGraphRun.task_graph_run_id.desc())
            .first()
        )
        graph_run_id = graph_run.task_graph_run_id if graph_run else None
        graph_status = graph_run.graph_status if graph_run else None
        # Graph definition: prefer the run's graph_id, else latest non-superseded for the stage.
        graph_id = graph_run.task_graph_id if graph_run else None
        graph = None
        if graph_id is not None:
            graph = db.get(TaskGraph, graph_id)
        if graph is None:
            graph = (
                db.query(TaskGraph)
                .filter(TaskGraph.run_id == run_id, TaskGraph.stage == stage,
                        TaskGraph.graph_status != "superseded")
                .order_by(TaskGraph.task_graph_id.desc())
                .first()
            )
        if graph is None:
            return SuccessEnvelope(
                data={"graph_id": None, "graph_run_id": None, "graph_status": None,
                      "nodes": []},
                meta=Meta(source_status="real"),
            )
        nodes_def = (
            db.query(TaskNode)
            .filter(TaskNode.task_graph_id == graph.task_graph_id)
            .order_by(TaskNode.node_id)
            .all()
        )
        # Latest run per node definition (if a run exists).
        node_status: dict[str, dict] = {}
        if graph_run_id:
            runs = (
                db.query(TaskNodeRun)
                .filter(TaskNodeRun.task_graph_run_id == graph_run_id)
                .order_by(TaskNodeRun.started_at.asc().nullslast())
                .all()
            )
            for r in runs:
                node_status[r.node_id] = {"node_status": r.node_status,
                                          "retry_count": r.retry_count or 0}
        nodes = []
        for nd in nodes_def:
            st = node_status.get(nd.node_id, {})
            nodes.append({
                "node_id": nd.node_id,
                "node_type": nd.node_type or "execution",
                "title": nd.title or nd.node_id,
                "status": st.get("node_status") or "pending",
                "retry_count": st.get("retry_count", 0),
            })
        return SuccessEnvelope(
            data={"graph_id": graph.task_graph_id, "graph_run_id": graph_run_id,
                  "graph_status": graph_status, "nodes": nodes},
            meta=Meta(source_status="real"),
        )
    finally:
        db.close()


# ── R17-6: background graph runner ──────────────────────────────────────────


import threading as _threading

# Registry of outstanding background graph futures. Production never reads this
# (fire-and-forget), but the test isolation teardown drains it (drain_graph_tasks)
# BEFORE restoring the global settings.database_url — otherwise a still-running
# daemon thread can lazily call get_engine()/get_services() AFTER the redirect is
# removed and bind the module-global engine to the REAL .data/rebuild.db, which the
# next test's isolation guard then (correctly) rejects. See V-R17-1B-7.
_graph_tasks: list = []
_graph_tasks_lock = _threading.Lock()


def _ensure_graph_task(coro):
    """Run a graph-coroutine in a dedicated daemon thread with its own event loop.

    Why a thread instead of loop.create_task():
      - In production (FastAPI async), the graph can run for minutes (LLM calls,
        source materialization). Offloading to a thread keeps the event loop free
        for health checks, SSE, and other requests.
      - In tests (sync TestClient), there is no running loop to tick between requests,
        so loop.create_task() would never actually run the coroutine. A dedicated
        thread with asyncio.run() drives the graph to completion regardless.

    The returned Future supports .result(timeout) for tests that want to wait.
    """
    import concurrent.futures
    def _runner():
        return _asyncio.run(coro)
    fut = concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(_runner)
    # Register the future so tests can drain it before tearing down DB isolation.
    # Prune finished futures so the list does not grow unbounded in long-lived
    # production processes.
    with _graph_tasks_lock:
        _graph_tasks.append(fut)
        _graph_tasks[:] = [f for f in _graph_tasks if not f.done()]
    return fut


def drain_graph_tasks(timeout: float = 30.0) -> None:
    """Test-only: block until all outstanding background graph futures finish.

    Called by the test isolation teardown (conftest.isolated_data) BEFORE it
    restores the global settings.database_url, guaranteeing no daemon thread lazily
    binds the module engine to the real DB after the redirect is removed. Production
    never calls this — fire-and-forget semantics are unchanged there.
    """
    with _graph_tasks_lock:
        pending = [f for f in _graph_tasks if not f.done()]
    for f in pending:
        try:
            f.result(timeout=timeout)
        except Exception:
            # advisory：后台图运行失败已在 _run_graph_bg 内部 logger.error + audit 记录（见下）；
            # 此处仅需确认线程已停止触碰全局状态，重复发声无益。
            logger.debug("drain_graph_tasks: 后台图 future 以异常结束（已在 _run_graph_bg 记录）", exc_info=True)
    with _graph_tasks_lock:
        _graph_tasks[:] = [f for f in _graph_tasks if not f.done()]


async def _run_graph_bg(run_id: str, decision: str | None, project_id: str, stage: str):
    """Background graph drive: run to completion, log and audit on failure.
    Mirrors the post-resume DB sync the sync path used to do, keeping
    project.state fresh once the graph actually completes.

    decision semantics:
      - str ("approve"/"reject"/"request_changes"): resume a graph paused at a Gate
        interrupt with the user's decision (Command(resume=decision)). This is the
        promotion-decision path.
      - None: continue a graph that was interrupted mid-work (no pending user Gate) —
        used by the WP-8 restart-recovery scheduler to resume an orphaned "running" run
        from its last durable checkpoint (ainvoke(None) re-runs the pending work node).
        Never fabricates a Gate decision.

    NOTE: This runs in a dedicated thread with its own event loop (see
    _ensure_graph_task). LangGraph's async SqliteSaver binds its internal
    asyncio.Lock + connection to the loop that first creates it. We therefore run
    the resume on an ISOLATED checkpointer+graph bound to THIS thread's loop and
    NEVER touch the process-global checkpointer / FlowRuntime — those stay bound to
    the main (uvicorn / TestClient) loop and back concurrent graph/state reads. The
    old approach closed+reopened the global singleton here, which left it bound to
    this throwaway loop; once the loop dies at asyncio.run() exit, the next
    main-loop read hit "Cannot operate on a closed database". Writes commit to the
    shared sqlite file, so the main-loop checkpointer still sees the advanced state.
    """
    svc = get_services()
    try:
        # NEW-01: the graph is resuming and will execute stage work → the Run is running.
        # Sync the DB Run row immediately so status queries don't show a stale value while
        # the background graph advances. Best-effort — a status write failure must not
        # abort the resume, but it must be voiced (state drift is a correctness concern).
        try:
            svc.run_service.set_run_status(run_id, "running")
        except Exception:
            logger.warning("图 resume 前同步 run_status=running 失败 run=%s", run_id, exc_info=True)
        # Fix: 立即同步 stage_status=in_progress，消除批准 plan gate 后
        # stage_status.p0 仍为 "pending" 的窗口（前端据此误判 P0 未启动，重新显示欢迎页）。
        # 图完成后的最终同步（L351-356）保持不变，此处仅关闭竞态窗口。
        try:
            svc.run_service.set_stage_status(run_id, stage, "in_progress")
        except Exception:
            logger.warning("图 resume 前同步 stage_status=in_progress 失败 run=%s stage=%s",
                           run_id, stage, exc_info=True)

        from app.graph.checkpoint import open_standalone_checkpointer, thread_config
        from app.graph.graph import build_graph
        from langgraph.types import Command
        conn, saver = await open_standalone_checkpointer()
        try:
            g = build_graph().compile(checkpointer=saver)
            # decision=None → continue an orphaned mid-work checkpoint (WP-8 recovery);
            # a str decision → resume a Gate interrupt with the user's decision.
            payload = Command(resume=decision) if decision is not None else None
            graph_state = await g.ainvoke(payload,
                                          config=thread_config(run_id))
        finally:
            await conn.close()
        nxt = graph_state.get("current_stage")
        # NEW-02: reflect the REAL waiting Gate produced by the resumed graph. When a
        # stage node interrupts on a fresh pending Gate, project.active_gate must point at
        # that gate_id (was hardcoded "" here, so the frontend lost the active Gate after
        # every promotion). Empty pending_gate → clear it (terminal / no gate waiting).
        pending_gate = graph_state.get("pending_gate") or {}
        active_gate_id = pending_gate.get("gate_id") or "" if isinstance(pending_gate, dict) else ""
        if nxt:
            try:
                svc.project_service.update(project_id, current_stage=nxt,
                                           active_gate=active_gate_id)
            except Exception:
                # 发声：图已推进但 project.state 未同步会造成状态漂移（前端仍显示旧阶段）。
                logger.warning("图完成后同步 project.current_stage 失败 run=%s stage=%s",
                               run_id, nxt, exc_info=True)
        for st, status in (graph_state.get("stage_status") or {}).items():
            try:
                svc.run_service.set_stage_status(run_id, st, status)
            except Exception:
                # 发声：stage_status 未持久化会造成状态漂移，须可见。
                logger.warning("图完成后同步 stage_status 失败 run=%s stage=%s", run_id, st, exc_info=True)
        # NEW-01: sync the terminal / waiting run_status from the resolved graph state.
        # The gate node sets run_status=completed (final approve) or blocked (reject);
        # otherwise a pending Gate means the run is waiting_gate; else it is still running.
        graph_run_status = graph_state.get("run_status")
        if graph_run_status in ("completed", "blocked"):
            final_run_status = graph_run_status
        elif active_gate_id:
            final_run_status = "waiting_gate"
        else:
            final_run_status = "running"
        try:
            svc.run_service.set_run_status(run_id, final_run_status)
        except Exception:
            logger.warning("图完成后同步 run_status 失败 run=%s status=%s",
                           run_id, final_run_status, exc_info=True)
    except Exception as e:
        logger.error("background graph run failed run=%s: %s", run_id, e, exc_info=True)
        # NEW-01: a failed background resume must leave the Run in an explicit failed
        # state (not silently stuck at "running") so the frontend can surface the failure
        # honestly (D-097: failures must be visible, never masked as success/progress).
        try:
            svc.run_service.set_run_status(run_id, "failed")
        except Exception:
            logger.warning("图失败后同步 run_status=failed 失败 run=%s", run_id, exc_info=True)
        try:
            svc.trace_writer.write("graph_error", action="graph_bg_failed",
                                   summary=f"Graph background resume failed: {e}",
                                   project_id=project_id, run_id=run_id, stage=stage)
        except Exception:
            # advisory：主错误已在上方 logger.error 记录；此处仅是补写 trace，失败不影响主发声。
            logger.debug("记录 graph_bg_failed trace 失败 run=%s", run_id, exc_info=True)
