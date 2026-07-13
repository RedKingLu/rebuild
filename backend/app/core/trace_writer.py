"""Trace writer — records execution/action traces to in-memory store + file.

R4: In-memory (volatile) store for instant API queries.
R8: Adds file persistence — writes to project workspace .rebuild/traces.jsonl.
    File persistence ensures traces survive restart.
"""

import json
import logging
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("rebuild.trace_writer")

MAX_TRACES = 10000  # in-memory cap to prevent unbounded growth


class TraceWriter:
    """Trace event store — in-memory for API queries + file persistence (R8)."""

    def __init__(self):
        self._traces: deque[dict] = deque(maxlen=MAX_TRACES)

    def write(
        self,
        trace_type: str,
        *,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        action: str = "",
        summary: str = "",
        graph_status: str = "not_connected",
        transition_mode: str = "unknown",
        request_id: Optional[str] = None,
        project_id: Optional[str] = None,
        **extra,
    ) -> dict:
        """Record a trace event. Returns the trace dict.

        R8: Also appends to .rebuild/traces.jsonl if project_id is set and workspace exists.
        """
        # R9-3A: Determine persistence dynamically instead of hardcoding "file+memory".
        # If project_id is None, file persistence is impossible → "memory" only.
        # If project_id is set and file write succeeds → "file+memory".
        # If project_id is set but file write fails → "memory" (honest marking).
        file_written = False
        if project_id:
            try:
                from app.core.config import settings
                traces_file = Path(settings.workspace_dir) / "projects" / project_id / ".rebuild" / "traces.jsonl"
                traces_file.parent.mkdir(parents=True, exist_ok=True)
                with open(traces_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "trace_id": f"trace-{uuid.uuid4().hex[:12]}",
                        "trace_type": trace_type,
                        "run_id": run_id,
                        "project_id": project_id,
                        "stage": stage,
                        "action": action,
                        "summary": summary,
                        "graph_status": graph_status,
                        "transition_mode": transition_mode,
                        "request_id": request_id,
                        "persistence": "file+memory",
                        "created_at": _now(),
                        **extra,
                    }, ensure_ascii=False) + "\n")
                file_written = True
            except Exception:
                # 发声：trace 文件持久化失败须可见；persistence 字段随后诚实降级为 "memory"，
                # 故不抛出，但不得静默吞噬失败原因。
                logger.warning("trace 文件持久化失败 project=%s type=%s", project_id, trace_type, exc_info=True)

        persistence = "file+memory" if file_written else "memory"

        trace = {
            "trace_id": f"trace-{uuid.uuid4().hex[:12]}",
            "trace_type": trace_type,
            "run_id": run_id,
            "project_id": project_id,
            "stage": stage,
            "action": action,
            "summary": summary,
            "graph_status": graph_status,
            "transition_mode": transition_mode,
            "request_id": request_id,
            "persistence": persistence,
            "created_at": _now(),
            **extra,
        }
        self._traces.append(trace)

        return trace

    def query(
        self,
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        trace_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query traces with optional filters (most recent first).

        R9 P1-3: Also reads from .rebuild/traces.jsonl so traces survive restart.
        """
        results = list(self._traces)

        # Read persisted traces from jsonl file (survives restart)
        if project_id:
            try:
                from app.core.config import settings
                traces_file = Path(settings.workspace_dir) / "projects" / project_id / ".rebuild" / "traces.jsonl"
                if traces_file.exists():
                    seen_ids = {t.get("trace_id") for t in results}
                    with open(traces_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                t = json.loads(line)
                                tid = t.get("trace_id", "")
                                if tid and tid not in seen_ids:
                                    seen_ids.add(tid)
                                    results.append(t)
                            except json.JSONDecodeError:
                                # 发声：trace jsonl 损坏行代表 trace 数据丢失，须可见。
                                logger.warning("trace jsonl 存在损坏行，已跳过 project=%s", project_id)
            except Exception:
                # 发声：读取持久化 trace 失败会让查询静默返回不完整结果（看似完整）。
                logger.warning("读取持久化 trace 失败 project=%s", project_id, exc_info=True)

        if project_id:
            results = [t for t in results if t.get("project_id") == project_id]
        if run_id:
            results = [t for t in results if t.get("run_id") == run_id]
        if stage:
            results = [t for t in results if t.get("stage") == stage]
        if trace_type:
            results = [t for t in results if t.get("trace_type") == trace_type]
        results.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return results[:limit]

    def clear(self):
        """Clear all traces (for test isolation)."""
        self._traces.clear()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
