"""Graph placeholder — documents LangGraph integration points for future stages.

R4: NO LangGraph code is executed. This module exists solely to:
1. Prevent the services layer from drifting into a self-built state machine.
2. Document where future LangGraph integration should occur.
3. Provide a runtime-visible marker that graph capability is not connected.

真实 LangGraph 主编排接入由后续主链路真实化阶段确定；R4 仅预留 graph/orchestration 边界。
未经阶段确认，R4 或 R5 不得提前实现真实主编排。
"""


class GraphPlaceholder:
    """R4 placeholder for future LangGraph StateGraph integration.

    Future integration points (NOT implemented in R4):
    1. Replace this module with real graph.py (StateGraph build + compile).
    2. Implement nodes/ directory (P0-P6 node functions).
    3. Connect SqliteSaver checkpoint (reference: V26.0 flow/graph.py).
    4. Implement FlowRuntime thin wrapper (reference: V26.0 flow/runtime.py).
    5. Load status enums from core/status.py.
    6. Migrate SSE mock events to real LangGraph events.

    Reference lessons from V10: Do NOT build a self-built state machine
    as an alternative to LangGraph. V10's migration from a self-built
    orchestrator to LangGraph cost 8 version iterations (R16-V1 through R16-V9).
    """

    graph_capability_status: str = "not_connected"
    transition_mode: str = "mock"

    @staticmethod
    def info() -> dict:
        return {
            "graph_capability_status": "not_connected",
            "transition_mode": "mock",
            "r4_note": (
                "R4 provides API skeleton with mock transitions. "
                "Real LangGraph orchestration will be integrated in a "
                "future main-chain realization stage, not before explicit "
                "stage confirmation."
            ),
        }
