"""FlowRuntime — thin wrapper over the compiled orchestration graph (R9-5-1, T6).

Lazily compiles the StateGraph with the async SqliteSaver checkpointer on first use
(checkpointer must exist before compile, and creating it needs a running loop).
thread_id == run_id throughout (checkpoint.thread_config).

Exposes:
  start(run_id, init_state)        — begin a run; returns state (pauses at first Gate interrupt)
  resume(run_id, decision)         — resume after a Gate decision (approve/reject/request_changes)
  get_state(run_id)                — current checkpointed state snapshot
  astream_events(run_id, ...)      — real LangGraph event stream (backs SSE, R9-5-1 T13)
  capability_status()              — "live" once compiled, else "degraded" (公理4, no hardcode)
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from langgraph.types import Command

from app.graph.checkpoint import get_checkpointer, thread_config
from app.graph.graph import build_graph


class FlowRuntime:
    def __init__(self):
        self._compiled = None
        self._saver = None
        self._lock = asyncio.Lock()

    async def _graph(self):
        # R19-4：编译图把 checkpointer 编了进去并长期缓存。get_checkpointer() 现在会在
        # 健康检查失败时重建单例（例如 checkpoints 表被删、连接已死），此时旧编译图仍握着
        # 那条已弃用的 saver —— checkpoint 层"恢复"了而生产调用方仍不可用，即假恢复。
        # 故每次取图都比对 saver 身份，换了就重新编译（健康时 get_checkpointer 只多一次
        # 亚毫秒级探针查询，返回的是同一对象，不触发重编译）。
        saver = await get_checkpointer()
        if self._compiled is None or self._saver is not saver:
            async with self._lock:
                if self._compiled is None or self._saver is not saver:
                    self._compiled = build_graph().compile(checkpointer=saver)
                    self._saver = saver
        return self._compiled

    def capability_status(self) -> str:
        # "live" only when a real graph has been compiled; never hardcoded optimistic.
        return "live" if self._compiled is not None else "degraded"

    async def start(self, run_id: str, init_state: dict) -> dict:
        g = await self._graph()
        return await g.ainvoke(init_state, config=thread_config(run_id))

    async def resume(self, run_id: str, decision: str) -> dict:
        g = await self._graph()
        return await g.ainvoke(Command(resume=decision), config=thread_config(run_id))

    async def get_state(self, run_id: str):
        g = await self._graph()
        return await g.aget_state(thread_config(run_id))

    async def astream_events(self, run_id: str, init_state: Optional[dict] = None,
                             resume_decision: Optional[str] = None) -> AsyncIterator[dict]:
        """Stream real LangGraph events for a start or resume drive (backs SSE).

        Pass init_state to drive a fresh start, or resume_decision to drive a resume.
        """
        g = await self._graph()
        payload = (Command(resume=resume_decision)
                   if resume_decision is not None else init_state)
        async for ev in g.astream_events(payload, config=thread_config(run_id),
                                         version="v2"):
            yield ev


# process singleton accessor (also mounted on Services in dependencies.py)
_runtime: Optional[FlowRuntime] = None


def get_flow_runtime() -> FlowRuntime:
    global _runtime
    if _runtime is None:
        _runtime = FlowRuntime()
    return _runtime


async def graph_thread_active(run_id: str) -> bool:
    """True iff a checkpoint thread for `run_id` exists AND is paused at an interrupt.

    Used by the legacy run-lifecycle / promotion-decision routes (W9/W10) to decide
    whether a decision should drive the graph via resume (single thread, no double
    advancement) or fall back to direct state advancement for non-graph runs.
    Any error (no checkpointer, never-started thread) → False (legacy path).
    """
    try:
        snap = await get_flow_runtime().get_state(run_id)
        return bool(getattr(snap, "next", ()))
    except Exception:
        return False


_cap_probe: Optional[str] = None


def graph_capability_probe() -> str:
    """Real probe of orchestration capability: 'live' if the StateGraph builds, else 'degraded'.

    Replaces the hardcoded 'not_connected'/'future_r10' optimism/pessimism (公理4/G7).
    Cached after first probe (graph topology is static).
    """
    global _cap_probe
    if _cap_probe is None:
        try:
            build_graph()
            _cap_probe = "live"
        except Exception:
            _cap_probe = "degraded"
    return _cap_probe


def reset_flow_runtime_for_test() -> None:
    global _runtime, _cap_probe
    _runtime = None
    _cap_probe = None
