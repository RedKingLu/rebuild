"""rebuild LangGraph orchestration package (R9-5-1).

Real StateGraph spine replacing the R4 placeholders:
  state.py        — GraphState with reducers
  nodes.py        — P0-P6 work/gate node functions (interrupt-based promotion Gates)
  graph.py        — StateGraph assembly
  checkpoint.py   — async SqliteSaver
  runtime.py      — FlowRuntime (lazy compile, start/resume/astream)
  stage_loop.py   — generic intra-stage small loop (D-091)
  stage_reports.py— three review reports per stage (D-092)
"""

from app.graph.state import GraphState, STAGE_ORDER, next_stage
from app.graph.graph import build_graph
from app.graph.runtime import FlowRuntime, get_flow_runtime

__all__ = [
    "GraphState", "STAGE_ORDER", "next_stage",
    "build_graph", "FlowRuntime", "get_flow_runtime",
]
