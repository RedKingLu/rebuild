"""Run API routes."""

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.run import RunCreate, RunResponse, RunResumeRequest, RunListResponse
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects/{project_id}/runs", tags=["runs"])


def _svc():
    return get_services()


@router.get("")
async def list_runs(project_id: str):
    svc = _svc()
    runs = svc.run_service.list_by_project(project_id)
    svc.trace_writer.write("state_change", action="list_runs",
                           summary=f"Listed {len(runs)} runs", project_id=project_id)
    return SuccessEnvelope(
        data=RunListResponse(runs=runs, total=len(runs), meta=Meta()),
        meta=Meta(),
    )


@router.post("")
async def create_run(project_id: str, req: RunCreate):
    svc = _svc()
    project = svc.project_service.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    run = svc.run_service.create(project_id, req)
    svc.trace_writer.write("state_change", action="create_run",
                           summary=f"Created run {run.run_id}", project_id=project_id, run_id=run.run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.get("/{run_id}")
async def get_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.get(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="get_run",
                           summary=f"Got run {run_id}", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/start")
async def start_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.start(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="start_run",
                           summary=f"Started run {run_id}", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/pause")
async def pause_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.pause(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="pause_run",
                           summary=f"Paused run {run_id}", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/cancel")
async def cancel_run(project_id: str, run_id: str):
    svc = _svc()
    run = svc.run_service.cancel(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")
    svc.trace_writer.write("state_change", action="cancel_run",
                           summary=f"Canceled run {run_id}", project_id=project_id, run_id=run_id)
    return SuccessEnvelope(data=run, meta=Meta())


@router.post("/{run_id}/resume")
async def resume_run(project_id: str, run_id: str, req: RunResumeRequest):
    """Resume a run (**测试/调试端点，无生产调用方**). W10: when a paused graph checkpoint
    thread exists for this run, the resume is driven through FlowRuntime (single thread,
    thread_id=run_id) so the graph advances; run_service keeps the run status row in sync.
    For non-graph runs (legacy/manual) it is a plain status transition. No mock Trace text
    (公理4/G7).

    **无生产调用方（2026-09-15 全仓实测）**：命中的调用方只有 `tests/test_runs.py` 与
    `tests/test_w9w10_graph_routes.py`；前端 `services/runService.ts` 导出了 `resumeRun()`
    但**全仓无任何组件调用它**（dormant wrapper）。生产路径请用
    `POST /gates/{gate_id}/decision` 或 `POST /stages/{stage}/promotion-decision`。

    B-ACC-PROMOTION-DECISION-NOGUARD 站点②守卫：本端点把 `req.decision` 注入图，却
    **完全没有 Gate 概念**（连 gate_id 字段都不存在）⇒ 结构上不可能判定决策要作用于哪个
    Gate，图恢复后会把它写到【图自己暂停的那个】Gate 上。「恢复一个 run」与「决策一个
    Gate」是两件事，前者不该能替后者做决定 ⇒ 图正暂停在某个 Gate 上时，本端点一律 409
    拒绝，不注入任何决策、不做状态变更。图停在工作节点（WP-8 中断续跑）或无图线程时，
    行为不变。"""
    from app.graph.runtime import get_flow_runtime, graph_thread_active, graph_pending_gate_ids

    svc = _svc()
    run = svc.run_service.get(run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found")

    # 同一性判据复用 graph_pending_gate_ids（与 routes_gates / routes_stages 同一份，
    # 不另造）。读失败 → 空集（fail-closed 的方向在此是"不拒绝但也不注入"：见下）。
    try:
        paused_gate_ids = await graph_pending_gate_ids(run_id)
    except Exception:
        paused_gate_ids = frozenset()
    if paused_gate_ids:
        raise HTTPException(
            409,
            f"run {run_id} 的图当前正暂停在 Gate {sorted(paused_gate_ids)} 上：本端点"
            f"（run 恢复）不能替 Gate 做决策——它无法指名被决策的对象，决策会被写到图自己"
            f"暂停的那个 Gate 上（B-ACC-PROMOTION-DECISION-NOGUARD 站点②）。"
            f"请改用 POST /api/projects/{project_id}/gates/{{gate_id}}/decision "
            f"或 POST /api/projects/{project_id}/runs/{run_id}/stages/{{stage}}/promotion-decision。")

    driven_by_graph = False
    # 走到这里说明「图没有暂停在任何 Gate 上」。仍可能停在工作节点（WP-8 中断续跑），
    # 那不是 Gate 决策，照旧允许恢复（行为不变）。
    if await graph_thread_active(run_id):
        try:
            await get_flow_runtime().resume(run_id, req.decision)
            driven_by_graph = True
        except Exception:
            driven_by_graph = False

    run = svc.run_service.resume(run_id)
    svc.trace_writer.write(
        "state_change", action="resume_run",
        summary=f"Resumed run {run_id} (decision={req.decision}, "
                f"{'graph-driven' if driven_by_graph else 'state-only'})",
        project_id=project_id, run_id=run_id,
        extras={"decision": req.decision, "graph_driven": driven_by_graph},
    )
    return SuccessEnvelope(data=run, meta=Meta())
