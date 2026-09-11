"""GraphState — real LangGraph state contract with reducers (R9-5-1, D-085).

Replaces the R4 placeholder `contracts.py` (bare TypedDict, no reducers).
This is the live state contract for the compiled StateGraph. List domains
accumulate across nodes (operator.add); dict domains shallow-merge; scalars
are last-write-wins (LangGraph default, no Annotated).

权威源：文档/03-流程与运行时/08-状态与数据模型.md（字段语义）。
红线：transition_mode/graph_capability_status 取真实值，不写死 "mock"/"not_connected"
（公理4 能力诚实）——graph_capability_status 由"图是否 compiled"运行期探测得出。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Dict, List, Literal, Optional, TypedDict


def merge_dict(left: Optional[dict], right: Optional[dict]) -> dict:
    """Shallow-merge reducer for dict domains (stage_status / gates).

    None-safe; right overrides left on key collision.
    """
    out: dict = dict(left or {})
    if right:
        out.update(right)
    return out


class GraphState(TypedDict, total=False):
    """Live state contract for the rebuild P0-P6 orchestration graph.

    Nodes return *increment* dicts; LangGraph applies the reducers below to
    fold them into the channel state. thread_id == run_id (see checkpoint.py).
    """

    # --- identity / pointers (scalar, last-write-wins) ---
    run_id: str
    project_id: str
    run_goal: str
    current_stage: str  # p0-p6
    run_status: str
    source_type: Optional[str]   # "manual" / "git" / "github" / "local_dir" (for P0 handler)
    source_config: Optional[Dict]  # source-specific config dict (for P0 handler)

    # --- accumulating domains (reducer = add) ---
    artifacts: Annotated[List[Dict], add]
    evidence: Annotated[List[Dict], add]
    events: Annotated[List[Dict], add]  # Trace event mirror
    audit: Annotated[List[Dict], add]
    errors: Annotated[List[Dict], add]

    # --- merge domains (reducer = shallow merge) ---
    stage_status: Annotated[Dict[str, str], merge_dict]
    gates: Annotated[Dict[str, Dict], merge_dict]

    # --- interrupt / resume bookkeeping (scalar) ---
    pending_gate: Optional[Dict]  # {gate_id, stage} set when a stage interrupts
    last_decision: Optional[str]  # approve / reject / request_changes (resume payload)
    # R17-X: plan_presentation 两阶段流程标记。True 表示接入计划已在 plan_presentation
    # gate 通过，work 节点应执行完整 StageLoop（而非 plan_only 模式）。
    plan_approved: Optional[bool]
    # V26.2 返工修复第 8 项（Q-RW-4）：P5 判定 rework_required 且用户批准该 promotion
    # Gate 时，make_router("p5") 据此路由回 "{rework_target_stage}_work" 而非正常晋级。
    # 标量、last-write-wins：由 {p5}_gate 节点在读到落盘的返工标记（gate_id 精确匹配，
    # 见 gate_service.read_p5_rework_marker）时显式写入；未匹配到时显式写 None（不能
    # 不写——否则会残留上一轮的旧值，误伤本轮真正通过的晋级判断）。只有 P5 的
    # gate()/router 读写这个字段，其它阶段完全不touch，不影响既有晋级路径。
    rework_target_stage: Optional[str]

    # --- capability / mode (scalar; real values, not placeholders) ---
    transition_mode: Literal["langgraph", "manual"]
    graph_capability_status: Literal["live", "degraded"]

    execution_mode: Literal["auto", "plan", "manual"]
    environment_profile_id: Optional[str]
    execution_session_refs: Annotated[List[str], add]
    final_delivery: Optional[Dict]


# Stage order — single source for graph edge wiring and promotion routing.
STAGE_ORDER: tuple[str, ...] = ("p0", "p1", "p2", "p3", "p4", "p5", "p6")


def next_stage(stage: str) -> Optional[str]:
    """Return the stage after `stage`, or None if `stage` is the last (p6)."""
    try:
        idx = STAGE_ORDER.index(stage)
    except ValueError:
        return None
    return STAGE_ORDER[idx + 1] if idx + 1 < len(STAGE_ORDER) else None
