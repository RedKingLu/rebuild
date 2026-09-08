"""Long-running task heartbeat registry (R21 / B-R20-NO-LONGTASK-MONITOR).

Problem this closes: the platform has NO way to tell "is this long-running background
task (e.g. the P4 execution worker's multi-round node loop) still alive, and when did
it last make progress". `GET /graph/state`'s `paused` field is unreliable (keyed off
`__interrupt__`, which does not persist across a checkpoint reload — it always reads
False on a fresh load). A prior real run went silently stuck for ~2 hours before a
human noticed; the platform itself had nothing to say about it.

Design boundaries (approved R21 batch — read before touching this file):
  - NOT a second orchestrator / scheduler (D-037 hard constraint): LangGraph remains
    the ONLY orchestrator. This module only records "a task made progress at time T"
    and, on query, classifies that as alive/stalled/unknown. It never retries, resumes,
    reschedules, or auto-continues anything.
  - NOT a new task queue (D-065 NIH): no Celery/Airflow, no new DB table, no Alembic
    migration. The heartbeat is a small JSON file under the project's workspace
    artifacts/ dir (D-099 platform-writable target), written through the SAME
    WorkspaceMediator write gate every other platform write goes through — never a
    raw filesystem write that bypasses it.
  - Honest tri-state, never a guess:
      alive   — a heartbeat was recorded within the configured threshold.
      stalled — a heartbeat exists but is older than the threshold (NOT "failed" —
                only "looks stuck"; deciding what to do about it is out of scope here).
      unknown — no heartbeat file at all (task predates this mechanism, or never
                started heartbeating), or the requested run_id does not match the
                recorded one. NEVER defaulted to "alive" for lack of data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.services.workspace_mediator import WorkspaceMediator
from app.services.workspace_service import workspace_path

logger = logging.getLogger(__name__)

# Single per-project heartbeat file — one record for whichever long-running task last
# reported progress. Under artifacts/ so it lands in the platform-writable, mediator-
# enforced write target shared with every other P4 artifact (D-099).
HEARTBEAT_REL_PATH = "artifacts/p4/_heartbeat.json"

_SCHEMA = "heartbeat_v1"


def write_heartbeat(project_id: str, run_id: str, task_kind: str, *,
                     current_node: Optional[str] = None) -> None:
    """Record that a long-running task made progress just now.

    Overwrites the single per-project heartbeat file (last-write-wins — this registry
    tracks liveness of the most recently active task, not a history). Routed through
    WorkspaceMediator.check_write so the write boundary (only output_code/, artifacts/,
    patches/ are writable; source/ stays read-only) is enforced exactly like every other
    platform write — this call never touches the filesystem directly.

    Advisory: a failed heartbeat write is logged (公理3, never silently swallowed) but
    never raised — a monitoring side-channel must not be able to break the actual task
    it is observing.
    """
    record = {
        "schema": _SCHEMA,
        "run_id": run_id,
        "task_kind": task_kind,
        "current_node": current_node,
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        mediator = WorkspaceMediator(str(workspace_path(project_id)))
        target, _risk = mediator.check_write(HEARTBEAT_REL_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("heartbeat write failed for project=%s run=%s task_kind=%s (advisory)",
                       project_id, run_id, task_kind, exc_info=True)


def read_heartbeat_status(project_id: str, run_id: Optional[str] = None, *,
                           stall_threshold_s: Optional[float] = None) -> dict:
    """Classify the current heartbeat for a project as alive / stalled / unknown.

    Args:
        project_id: project whose workspace heartbeat file to read.
        run_id: when given, the recorded heartbeat must belong to THIS run — a
            heartbeat from a different (e.g. older, already-finished) run is not
            evidence about the run being asked about, so it is reported "unknown"
            rather than compared against the threshold.
        stall_threshold_s: override the configured threshold (app.core.config.settings
            .p4_heartbeat_stall_threshold_s) for this call. Kept a plain parameter
            (not a hardcoded constant) so callers/tests can exercise both sides of the
            alive/stalled boundary without touching global config.

    Returns:
        {"status": "alive"|"stalled"|"unknown", "heartbeat": <record dict or None>,
         "age_seconds": float | None, "stall_threshold_s": float}
    """
    if stall_threshold_s is None:
        from app.core.config import settings
        stall_threshold_s = settings.p4_heartbeat_stall_threshold_s

    hb_file = workspace_path(project_id) / HEARTBEAT_REL_PATH
    if not hb_file.is_file():
        return {"status": "unknown", "heartbeat": None, "age_seconds": None,
                "stall_threshold_s": stall_threshold_s}

    try:
        record = json.loads(hb_file.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("heartbeat file unreadable/corrupt for project=%s (advisory)",
                       project_id, exc_info=True)
        return {"status": "unknown", "heartbeat": None, "age_seconds": None,
                "stall_threshold_s": stall_threshold_s}

    if not isinstance(record, dict):
        return {"status": "unknown", "heartbeat": None, "age_seconds": None,
                "stall_threshold_s": stall_threshold_s}

    if run_id and record.get("run_id") != run_id:
        # Data exists, but not FOR the run being asked about — honest unknown, not a
        # guess either way.
        return {"status": "unknown", "heartbeat": record, "age_seconds": None,
                "stall_threshold_s": stall_threshold_s}

    try:
        last = datetime.fromisoformat(record["last_heartbeat_at"])
    except Exception:
        logger.warning("heartbeat timestamp unparseable for project=%s (advisory)",
                       project_id, exc_info=True)
        return {"status": "unknown", "heartbeat": record, "age_seconds": None,
                "stall_threshold_s": stall_threshold_s}

    age_seconds = (datetime.now(timezone.utc) - last).total_seconds()
    status = "alive" if age_seconds <= stall_threshold_s else "stalled"
    return {"status": status, "heartbeat": record, "age_seconds": age_seconds,
            "stall_threshold_s": stall_threshold_s}
