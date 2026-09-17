"""Graph driver endpoints — drive the P0-P6 LangGraph over HTTP (R9-5-1, T10/T12 增量).

Additive endpoints (do NOT replace the legacy onboarding/profile endpoints yet — the full
cutover + gate-decision→resume coordinate with R9-5-7 HITL). These expose the real compiled
graph: start a run (p0_work → pause at promotion Gate), resume after a Gate decision, read the
checkpointed state. thread_id == run_id; state survives restart (checkpoint).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_services
from app.schemas.common import Meta, SuccessEnvelope
from app.schemas.run import RunCreate
from app.services.project_service import ProjectService
from app.services.run_service import RunService
from app.graph.runtime import get_flow_runtime

router = APIRouter(prefix="/projects", tags=["graph"])

_VALID_DECISIONS = {"approve", "reject", "request_changes"}


class GraphStartRequest(BaseModel):
    run_goal: str | None = None
    execution_mode: str = "plan"
    source_type: str | None = None


class GraphResumeRequest(BaseModel):
    run_id: str
    decision: str
    # B-ACC-PROMOTION-DECISION-NOGUARD 站点②：决策必须指名被决策的 Gate。
    # 【勿删】不指名时无法判定决策会落到哪个 Gate 上——图恢复后会把它写到【图自己
    # 暂停的那个】Gate 上（真跑已坐实同族缺陷造成一次未授权状态变更）。
    # Optional 是为了让"图停在工作节点/无图线程"的既有调试用法不必改；但图正暂停在
    # 某个 Gate 上时，不指名（或指名了别的 Gate）一律 409，见 graph_resume 守卫。
    gate_id: str | None = None


def _summary(state: dict) -> dict:
    state = state or {}
    return {
        "current_stage": state.get("current_stage"),
        "run_status": state.get("run_status"),
        "stage_status": state.get("stage_status", {}),
        "paused": state.get("__interrupt__") is not None,
        "pending_gate": state.get("pending_gate"),
        "last_decision": state.get("last_decision"),
        "artifacts_count": len(state.get("artifacts", [])),
        "events_count": len(state.get("events", [])),
        "graph_capability_status": get_flow_runtime().capability_status(),
    }


def _src_type(project) -> str:
    st = project.source_type
    return st.value if hasattr(st, "value") else str(st)


@router.post("/{project_id}/graph/start")
async def graph_start(project_id: str, req: GraphStartRequest, db: Session = Depends(get_db)):
    """Create a Run (graph thread) and start the P0-P6 graph; pauses at the P0 promotion Gate.

    仅供调试 / 测试：生产环境 P0 启动一律走 POST /projects/{id}/onboarding/execute
    （规范主入口，B-R17X-DUALENTRY-2）。本端点保留可用，仅用于直接驱动图的调试与测试场景。
    """
    svc = ProjectService(db)
    deps = get_services()
    project = svc.get(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    run = RunService(deps).create(
        project_id,
        RunCreate(run_goal=req.run_goal or f"P0-P6: {project.name}", mode=req.execution_mode),
    )
    svc.update(project_id, current_run_id=run.run_id, current_stage="p0")

    init_state = {
        "run_id": run.run_id, "project_id": project_id,
        "run_goal": run.run_goal, "run_status": "running",
        "source_type": req.source_type or _src_type(project),
        "source_config": project.source_config or {},
        "execution_mode": req.execution_mode,
        "stage_status": {"p0": "in_progress"},
    }
    state = await get_flow_runtime().start(run.run_id, init_state)
    return SuccessEnvelope(data={"run_id": run.run_id, **_summary(state)}, meta=Meta())


@router.post("/{project_id}/graph/resume")
async def graph_resume(project_id: str, req: GraphResumeRequest, db: Session = Depends(get_db)):
    """Resume a paused graph with a Gate decision (approve / reject / request_changes).

    **测试/调试端点，无生产调用方（2026-09-15 全仓实测）**：命中的调用方只有
    `tests/test_graph_api.py`。生产路径请用 `POST /gates/{gate_id}/decision` 或
    `POST /runs/{run_id}/stages/{stage}/promotion-decision`。

    B-ACC-PROMOTION-DECISION-NOGUARD 站点②守卫：图正暂停在某个 Gate 上时，本端点要求
    请求体用 `gate_id` **指名**被决策的那个 Gate，且它必须就是图的暂停点；不指名或指名
    另一个 Gate ⇒ 409，决策不注入。理由：不指名的决策会被图写到【它自己暂停的那个】Gate
    上，与调用方的意图无关——这正是本缺陷的形态。"""
    decision = (req.decision or "").strip().lower()
    if decision not in _VALID_DECISIONS:
        raise HTTPException(400, f"非法决策：{req.decision!r}（允许 {sorted(_VALID_DECISIONS)}）")
    svc = ProjectService(db)
    if svc.get(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # 同一性判据复用 graph_pending_gate_ids（与 routes_gates / routes_stages / routes_runs
    # 同一份，不另造）。读失败 → 空集：退回"图未暂停在 Gate 上"的既有语义，不臆断。
    from app.graph.runtime import graph_pending_gate_ids
    try:
        paused_gate_ids = await graph_pending_gate_ids(req.run_id)
    except Exception:
        paused_gate_ids = frozenset()
    requested_gate_id = (req.gate_id or "").strip()
    if paused_gate_ids and requested_gate_id not in paused_gate_ids:
        raise HTTPException(
            409,
            f"run {req.run_id} 的图当前暂停在 Gate {sorted(paused_gate_ids)} 上，"
            f"而本次请求" + (f"指名的是 {requested_gate_id}" if requested_gate_id else "未指名任何 gate_id")
            + "：拒绝注入决策——不指名（或指名另一个 Gate）的决策会被应用到图自己暂停的那个 "
            f"Gate 上（B-ACC-PROMOTION-DECISION-NOGUARD 站点②）。请在请求体带上 "
            f"gate_id，或改用 POST /api/projects/{project_id}/gates/{{gate_id}}/decision。")

    state = await get_flow_runtime().resume(req.run_id, decision)
    return SuccessEnvelope(data={"run_id": req.run_id, **_summary(state)}, meta=Meta())


@router.get("/{project_id}/graph/state")
async def graph_state(project_id: str, run_id: str = Query(...), db: Session = Depends(get_db)):
    """Read the checkpointed graph state snapshot for a run (survives restart)."""
    snap = await get_flow_runtime().get_state(run_id)
    return SuccessEnvelope(data={"run_id": run_id, **_summary(snap.values)}, meta=Meta())
