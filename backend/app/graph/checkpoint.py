"""Async SQLite checkpointer for the LangGraph orchestration graph (R9-5-1, T5).

Provides a process-singleton AsyncSqliteSaver bound to a dedicated sqlite file
`.data/graph_checkpoints.sqlite` — isolated from the business DB (rebuild.db) to
avoid the connection-pool coupling that caused B-DB-01 (RK-3 mitigation).

thread_id convention: thread_id == run_id (one graph thread per Run, D-085 / Q-4).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.config import settings

logger = logging.getLogger("rebuild.graph.checkpoint")

_CKPT_FILENAME = "graph_checkpoints.sqlite"

_conn: Optional[aiosqlite.Connection] = None
_saver: Optional[AsyncSqliteSaver] = None
_lock: Optional[asyncio.Lock] = None


def checkpoint_path() -> Path:
    p = settings.data_path
    p.mkdir(parents=True, exist_ok=True)
    return p / _CKPT_FILENAME


async def get_checkpointer() -> AsyncSqliteSaver:
    """Return the process-singleton AsyncSqliteSaver (lazy, table-initialised)."""
    global _conn, _saver, _lock
    if _saver is not None:
        return _saver
    # Lazy lock creation: bound to the CURRENT event loop.
    # Re-create if the existing lock belongs to a different loop (test isolation).
    try:
        if _lock is None:
            _lock = asyncio.Lock()
        # Quick test: try to get the running loop; if the lock's loop differs, recreate
        running = asyncio.get_running_loop()
        try:
            lock_loop = _lock._get_loop()  # type: ignore[attr-defined]
            if lock_loop is not running:
                _lock = asyncio.Lock()
        except Exception:
            _lock = asyncio.Lock()
    except RuntimeError:
        _lock = asyncio.Lock()

    async with _lock:
        if _saver is None:
            _conn = await aiosqlite.connect(str(checkpoint_path()))
            saver = AsyncSqliteSaver(_conn)
            await saver.setup()
            _saver = saver
    return _saver


def thread_config(run_id: str) -> dict:
    """LangGraph config dict pinning the checkpoint thread to a run."""
    return {"configurable": {"thread_id": run_id}}


async def open_standalone_checkpointer() -> tuple[aiosqlite.Connection, AsyncSqliteSaver]:
    """Open a NON-singleton checkpointer bound to the CURRENT event loop.

    For background-thread graph drivers (routes_stages._run_graph_bg) that run on
    their own throwaway loop: they must NOT reuse or mutate the process-global
    singleton, which stays bound to the main/uvicorn loop and backs concurrent
    graph/state reads. Sharing one aiosqlite connection across loops (the old
    close+reopen-the-global hack) left the global singleton bound to the throwaway
    loop, which dies on asyncio.run() exit → "Cannot operate on a closed database"
    on the next main-loop read. The caller OWNS the returned connection and MUST
    close it (writes commit to the shared sqlite file, so the main-loop checkpointer
    still sees them via its own connection).
    """
    conn = await aiosqlite.connect(str(checkpoint_path()))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return conn, saver



async def close_checkpointer() -> None:
    """Close the singleton checkpointer connection (app shutdown / test teardown)."""
    global _conn, _saver, _lock
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            # advisory：应用关闭/测试拆卸时关闭 checkpointer 连接，失败不影响后续（连接随即置空）。
            logger.debug("关闭 checkpointer 连接失败（关闭/拆卸阶段，best-effort）", exc_info=True)
    _conn = None
    _saver = None
    _lock = None  # reset so next call creates a fresh lock on the current event loop


async def reset_checkpointer_for_test() -> None:
    """Test helper: drop the singleton so a fresh temp DB can be bound."""
    await close_checkpointer()
