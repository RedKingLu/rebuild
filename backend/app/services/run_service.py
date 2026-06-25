"""Run service — CRUD and lifecycle operations on mock runs.

R4: ALL state transitions are mock transitions (transition_mode="mock").
This service does NOT advance any real process flow.
"""

from typing import Optional

from app.repositories.fixtures import seed_runs
from app.schemas.run import RunResponse, RunCreate
from app.schemas.common import Meta


class RunService:
    def __init__(self, services):
        self._svc = services
        self._runs: dict[str, RunResponse] = {}
        self._seed()

    def _seed(self):
        for r in seed_runs():
            self._runs[r.run_id] = r

    def list_by_project(self, project_id: str) -> list[RunResponse]:
        return [r for r in self._runs.values() if r.project_id == project_id]

    def get(self, run_id: str) -> Optional[RunResponse]:
        return self._runs.get(run_id)

    def create(self, project_id: str, req: RunCreate) -> RunResponse:
        import uuid
        rid = f"run-{uuid.uuid4().hex[:6]}"
        r = RunResponse(
            run_id=rid,
            project_id=project_id,
            run_goal=req.run_goal,
            run_status="created",
            started_at=_now(),
            updated_at=_now(),
            stage_status={f"P{i}": "pending" for i in range(7)},
        )
        # Set P0 as not_started if this is a new run
        r.stage_status["P0"] = "not_started"
        self._runs[rid] = r
        return r

    def _mock_transition(self, run_id: str, new_status: str) -> Optional[RunResponse]:
        """Mock state transition — marks transition_mode=mock explicitly."""
        r = self._runs.get(run_id)
        if r is None:
            return None
        r.run_status = new_status
        r.updated_at = _now()
        r.transition_mode = "mock"
        return r

    def start(self, run_id: str) -> Optional[RunResponse]:
        return self._mock_transition(run_id, "running")

    def pause(self, run_id: str) -> Optional[RunResponse]:
        return self._mock_transition(run_id, "paused")

    def cancel(self, run_id: str) -> Optional[RunResponse]:
        return self._mock_transition(run_id, "canceled")

    def resume(self, run_id: str) -> Optional[RunResponse]:
        return self._mock_transition(run_id, "running")


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
