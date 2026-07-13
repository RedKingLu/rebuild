"""R17.2 OD-08 — SSE real state_changed diff events.

Verifies EventService.event_stream emits a real snapshot first, then a real
`state_changed` event when the LangGraph checkpoint snapshot genuinely changes
(current_stage p0 -> p1). Uses a fake FlowRuntime whose get_state returns a
changing snapshot sequence; no fabricated events, no real graph needed.
"""

import asyncio
import logging

import pytest

from app.services import event_service
from app.services.event_service import EventService


class _FakeSnap:
    def __init__(self, values):
        self.values = values


class _FakeRuntime:
    """get_state returns p0 on the first read (snapshot baseline), p1 afterwards."""

    def __init__(self):
        self._calls = 0

    async def get_state(self, run_id):
        self._calls += 1
        stage = "p0" if self._calls <= 1 else "p1"
        return _FakeSnap({
            "current_stage": stage,
            "run_status": "running",
            "stage_status": {stage: "in_progress"},
            "pending_gate": None,
        })


@pytest.fixture
def fast_poll(monkeypatch):
    fake = _FakeRuntime()
    monkeypatch.setattr(event_service, "get_flow_runtime", lambda: fake)
    monkeypatch.setattr(event_service, "POLL_SECONDS", 0.01)
    # keep heartbeat far away so it does not interleave before the diff
    monkeypatch.setattr(event_service, "HEARTBEAT_SECONDS", 1000)
    return fake


async def test_snapshot_then_state_changed_on_real_diff(fast_poll):
    svc = EventService(services=None)
    gen = svc.event_stream(project_id="proj-1", run_id="run-1")

    first = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert first["event_type"] == "snapshot"
    assert first["payload"]["current_stage"] == "p0"

    second = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert second["event_type"] == "state_changed"
    assert second["payload"]["current_stage"] == "p1"
    assert "current_stage" in second["payload"]["changed"]
    assert second["payload"]["run_id"] == "run-1"

    await gen.aclose()


async def test_no_run_id_snapshot_no_fabricated_change(monkeypatch):
    # No run_id: honest snapshot, and no state_changed is ever fabricated.
    monkeypatch.setattr(event_service, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(event_service, "HEARTBEAT_SECONDS", 0.01)
    svc = EventService(services=None)
    gen = svc.event_stream(project_id="proj-1", run_id=None)

    first = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert first["event_type"] == "snapshot"
    # no run_id -> no checkpoint fields, no state marker fabricated
    assert "current_stage" not in first["payload"]

    # next event with no run_id must be a heartbeat, never a state_changed
    nxt = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert nxt["event_type"] == "heartbeat"
    await gen.aclose()


async def test_get_state_error_is_warned_not_fabricated(monkeypatch, caplog):
    class _BoomRuntime:
        async def get_state(self, run_id):
            raise RuntimeError("checkpointer down")

    monkeypatch.setattr(event_service, "get_flow_runtime", lambda: _BoomRuntime())
    monkeypatch.setattr(event_service, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(event_service, "HEARTBEAT_SECONDS", 0.01)
    # Test isolation hardening: earlier tests may trigger alembic's fileConfig(),
    # which runs with disable_existing_loggers=True and marks this already-imported
    # logger .disabled=True (dropping all records). Re-enable it and force capture so
    # the WARNING assertion is stable regardless of global logging state.
    ev_logger = logging.getLogger("app.services.event_service")
    ev_logger.disabled = False
    ev_logger.propagate = True
    caplog.set_level(logging.WARNING, logger="app.services.event_service")

    svc = EventService(services=None)
    gen = svc.event_stream(project_id="proj-1", run_id="run-x")

    first = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert first["event_type"] == "snapshot"
    assert first["payload"].get("state") == "no_checkpoint"  # honest on error

    # on error, no state_changed is fabricated — only heartbeat continues
    nxt = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert nxt["event_type"] == "heartbeat"
    await gen.aclose()

    assert any("get_state failed" in r.message for r in caplog.records)
