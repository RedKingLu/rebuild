"""Test Trace writer and query."""


def test_trace_writer_records_requests(client):
    """Each API request should create a trace via the trace_writer."""
    # Make some requests
    client.get("/api/projects")
    client.get("/api/projects/proj-001")

    # Query traces
    resp = client.get("/api/projects/proj-001/trace")
    assert resp.status_code == 200
    data = resp.json()["data"]
    traces = data["traces"]
    assert len(traces) >= 1
    assert data["persistence"] == "volatile"


def test_trace_has_required_fields(client):
    client.get("/api/projects/proj-001")
    resp = client.get("/api/projects/proj-001/trace?limit=1")
    traces = resp.json()["data"]["traces"]
    if traces:
        t = traces[0]
        assert "trace_id" in t
        assert "trace_type" in t
        assert "summary" in t
        assert "graph_status" in t
        assert "transition_mode" in t
        assert t["graph_status"] == "not_connected"
        assert t["transition_mode"] == "mock"
        assert t["persistence"] == "volatile"


def test_trace_filter_by_type(client):
    client.get("/api/projects/proj-001")
    client.get("/api/projects/proj-001")
    resp = client.get("/api/projects/proj-001/trace?trace_type=state_change&limit=50")
    assert resp.status_code == 200


def test_trace_nonexistent(client):
    resp = client.get("/api/projects/proj-001/trace/trace-nonexistent")
    assert resp.status_code == 404
