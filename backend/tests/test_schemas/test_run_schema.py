"""Test Pydantic schema validation for Run schemas."""

from app.schemas.run import RunCreate, RunResponse


def test_run_create_valid():
    r = RunCreate(run_goal="Test goal", mode="plan")
    assert r.run_goal == "Test goal"


def test_run_response_has_graph_fields():
    r = RunResponse(
        run_id="r1",
        project_id="p1",
        run_goal="test",
        run_status="created",
        stage_status={"P0": "not_started"},
    )
    assert r.graph_capability_status == "not_connected"
    assert r.transition_mode == "unknown"
    assert r.checkpoint_ref is None
