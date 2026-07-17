"""Run service — DB-persisted CRUD and lifecycle operations (R9 P1-1).

Replaces the in-memory _runs dict with SQLAlchemy Run model.
"""

from typing import Optional
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.run import Run
from app.schemas.run import RunResponse, RunCreate
from app.schemas.common import Meta


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_to_response(r: Run) -> RunResponse:
    return RunResponse(
        run_id=r.run_id,
        project_id=r.project_id,
        run_goal=r.run_goal or "",
        run_status=r.run_status,
        current_stage=r.current_stage,
        execution_mode=r.execution_mode,
        stage_status=r.stage_status or {},
        source_status=r.source_status or "real",
        capability_status=r.capability_status or "available",
        transition_mode=r.transition_mode or "real",
        started_at=r.started_at.isoformat() if r.started_at else None,
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
        meta=Meta(),
    )


class RunService:
    def __init__(self, services):
        self._svc = services

    def _db(self) -> Session:
        from app.core.database import get_session
        return get_session()

    def list_by_project(self, project_id: str) -> list[RunResponse]:
        db = self._db()
        try:
            runs = db.query(Run).filter(Run.project_id == project_id).order_by(Run.started_at.desc()).all()
            return [_run_to_response(r) for r in runs]
        finally:
            db.close()

    def get(self, run_id: str) -> Optional[RunResponse]:
        db = self._db()
        try:
            r = db.get(Run, run_id)
            return _run_to_response(r) if r else None
        finally:
            db.close()

    def create(self, project_id: str, req: RunCreate) -> RunResponse:
        db = self._db()
        try:
            r = Run(
                project_id=project_id,
                run_goal=req.run_goal,
                run_status="created",
                execution_mode=req.mode,
                stage_status={"p0": "in_progress", "p1": "pending", "p2": "pending", "p3": "pending", "p4": "pending", "p5": "pending", "p6": "pending"},
                current_stage="p0",
                started_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(r)
            db.commit()
            db.refresh(r)
            # Trace
            if hasattr(self._svc, 'trace_writer'):
                self._svc.trace_writer.write(
                    "run_created", action="create_run",
                    summary=f"Run created: {req.run_goal}, mode={req.mode}",
                    project_id=project_id, run_id=r.run_id,
                )
            return _run_to_response(r)
        finally:
            db.close()

    def set_stage_status(self, run_id: str, stage_code: str, status: str) -> Optional[RunResponse]:
        db = self._db()
        try:
            r = db.get(Run, run_id)
            if r is None:
                return None
            # R17-2 V-R17-1B-4 修复：
            # 1) 去 .upper() — 与 Run.create 的小写 key（p0/p1/...）保持一致，避免大小写分裂
            # 2) 赋新 dict 而非就地 mutate — SQLAlchemy JSON 列不追踪就地 mutation，
            #    重赋新对象才触发 dirty → UPDATE
            new_ss = dict(r.stage_status or {})
            new_ss[stage_code.lower()] = status
            r.stage_status = new_ss
            r.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(r)
            return _run_to_response(r)
        finally:
            db.close()

    def start(self, run_id: str) -> Optional[RunResponse]:
        return self._transition(run_id, "running")

    def set_run_status(self, run_id: str, status: str) -> Optional[RunResponse]:
        """NEW-01: explicit run_status setter used by the background graph runner to
        keep the Run row in sync with the LangGraph checkpoint (running while resuming,
        waiting_gate when a Gate is pending, completed/blocked on terminal states)."""
        return self._transition(run_id, status)

    def pause(self, run_id: str) -> Optional[RunResponse]:
        return self._transition(run_id, "paused")

    def cancel(self, run_id: str) -> Optional[RunResponse]:
        return self._transition(run_id, "canceled")

    def resume(self, run_id: str) -> Optional[RunResponse]:
        return self._transition(run_id, "running")

    def _transition(self, run_id: str, new_status: str) -> Optional[RunResponse]:
        db = self._db()
        try:
            r = db.get(Run, run_id)
            if r is None:
                return None
            r.run_status = new_status
            r.updated_at = datetime.now(timezone.utc)
            r.transition_mode = "real"
            db.commit()
            db.refresh(r)
            return _run_to_response(r)
        finally:
            db.close()
