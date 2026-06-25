"""GraphState TypedDict — field-level placeholder for future LangGraph integration.

R4: PLACEHOLDER ONLY. No StateGraph is compiled. No graph is executed.
真实 LangGraph 主编排接入由后续主链路真实化阶段确定；R4 仅预留 graph/orchestration 边界。
未经阶段确认，R4 或 R5 不得提前实现真实主编排。
"""

from typing import TypedDict, List, Dict, Optional, Literal


class GraphState(TypedDict, total=False):
    """LangGraph 主编排图的状态契约（R4 占位，不实现 reducer）。

    This is a STRUCTURAL REFERENCE only. All fields are defined
    here to establish the contract surface. No LangGraph reducer
    functions are implemented in R4.

    Future implementation reference: V26.0 flow/state.py (TypedDict
    + Annotated reducers pattern).
    """

    run_id: str
    project_id: str
    run_goal: str
    current_stage: str  # P0-P6
    stage_status: Dict[str, str]
    run_status: str

    artifacts: List[Dict]
    evidence: List[Dict]
    events: List[Dict]  # Trace
    audit: List[Dict]
    gates: Dict[str, Dict]
    errors: List[Dict]

    checkpoint_ref: Optional[str]
    interrupt_ref: Optional[str]
    resume_ref: Optional[str]
    pending_interrupt: Optional[Dict]

    transition_mode: Literal["mock", "langgraph", "manual"]
    graph_capability_status: Literal["not_connected", "mock_graph", "placeholder", "live"]

    execution_mode: Literal["auto", "plan", "manual"]
    environment_profile_id: Optional[str]
    execution_session_refs: List[str]
    final_delivery: Optional[Dict]
