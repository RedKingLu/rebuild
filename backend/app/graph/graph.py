"""StateGraph assembly for the P0-P6 orchestration (R9-5-1, T4).

Replaces graph/placeholders.py. Topology:

    START → p0_work → p0_gate ──approve──→ p1_work → p1_gate ──approve──→ … → p6_gate ──approve──→ END
                          │ reject → END
                          │ request_changes → p0_work (rework)

Compiled with an async SqliteSaver checkpointer in runtime.py (lazy), so promotion
Gates can `interrupt` and `resume` survive process restart (thread_id == run_id).
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from app.graph.state import GraphState, STAGE_ORDER
from app.graph.nodes import make_work_node, make_gate_node, make_router


def build_graph() -> StateGraph:
    """Build the uncompiled StateGraph topology (compile happens in runtime)."""
    g = StateGraph(GraphState)

    for s in STAGE_ORDER:
        g.add_node(f"{s}_work", make_work_node(s))
        g.add_node(f"{s}_gate", make_gate_node(s))

    g.add_edge(START, "p0_work")

    # path map shared by every gate's conditional edge: any work node, or END
    path_map = {f"{s}_work": f"{s}_work" for s in STAGE_ORDER}
    path_map["__end__"] = END

    for s in STAGE_ORDER:
        g.add_edge(f"{s}_work", f"{s}_gate")
        g.add_conditional_edges(f"{s}_gate", make_router(s), path_map)

    return g
