"""R9-5-1 阶段C tests — REAL P0/P1 business runs inside LangGraph nodes (D-085 core).

Proves the P0/P1 nodes carry real work (SourceMaterializer + FullStackProfiler) producing
real artifacts on disk, driven by the compiled graph with interrupt/resume — not stubs.
Uses a FakeGate backend to avoid DB coupling (RealGateBackend is exercised via routes).
"""

import json

import pytest

from app.graph import nodes
from app.graph.checkpoint import reset_checkpointer_for_test
from app.graph.runtime import get_flow_runtime, reset_flow_runtime_for_test
from app.graph.stage_handlers import RealP0Handler, RealP1Handler


class FakeGate:
    def __init__(self):
        self.created = []
        self._n = 0

    def create(self, *, project_id, run_id, stage, artifact_refs,
               gate_type="stage_promotion", metadata=None):
        self._n += 1
        gid = f"gate-{stage}-{self._n}"
        self.created.append((gid, stage, list(artifact_refs)))
        return gid

    def decide(self, *, gate_id, decision):
        pass


@pytest.fixture
async def realp0p1(tmp_path, monkeypatch):
    ckpt = tmp_path / "graph_checkpoints.sqlite"
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path", lambda: ckpt)
    ws_root = tmp_path / "ws"
    monkeypatch.setattr("app.services.workspace_service._workspace_root", lambda: ws_root)

    project_id = "proj-real"
    # init workspace + place a real source tree the profiler can identify
    from app.services import workspace_service
    workspace_service.init_workspace(project_id)
    src = workspace_service.workspace_path(project_id) / "source"
    (src / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"react": "^18"}}), encoding="utf-8")
    (src / "app.py").write_text("print('hello')\n", encoding="utf-8")

    nodes.clear_handlers()
    nodes.register_handler("p0", RealP0Handler(tracer=None, auditor=None))
    nodes.register_handler("p1", RealP1Handler(tracer=None, auditor=None))
    gb = FakeGate()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)

    yield project_id, gb, ws_root
    await reset_checkpointer_for_test()
    reset_flow_runtime_for_test()
    nodes.clear_handlers()
    nodes.set_gate_backend(None)


async def test_p0_p1_real_business_in_graph(realp0p1):
    project_id, gb, ws_root = realp0p1
    await reset_checkpointer_for_test()
    reset_flow_runtime_for_test()
    rt = get_flow_runtime()

    init = {"run_id": "run-real", "project_id": project_id, "run_goal": "real",
            "run_status": "running", "source_type": "manual",
            "stage_status": {"p0": "in_progress"}}

    # P0 runs real materialize + intake, then pauses at promotion Gate
    s = await rt.start("run-real", init)
    assert "__interrupt__" in s
    art = ws_root / "projects" / project_id / "artifacts"
    assert (art / "p0" / "intake_report.json").exists(), "P0 produced a real intake_report"
    # three D-092 reports for p0
    assert (art / "p0_start_plan.json").exists()
    assert (art / "p0_construction.json").exists()
    assert (art / "p0_acceptance.json").exists()
    intake = json.loads((art / "p0" / "intake_report.json").read_text(encoding="utf-8"))
    assert intake["file_count"] >= 2

    # approve P0 → P1 runs real FullStackProfiler, then pauses at its Gate
    s = await rt.resume("run-real", "approve")
    assert "__interrupt__" in s
    # real profiler artifacts on disk (D-107: artifacts/p1/)
    assert (art / "p1" / "profiling_summary.md").exists(), "P1 produced real profiling summary"
    assert (art / "p1" / "p2_input_manifest.json").exists(), "P1 produced real P2 manifest"
    json_artifacts = list((art / "p1").glob("*.json"))
    assert len(json_artifacts) >= 5, "P1 produced multiple real identification JSONs"
    # p1 three reports
    assert (art / "p1_start_plan.json").exists()
    assert (art / "p1_acceptance.json").exists()

    # both stages created real promotion gates with attached report+domain refs
    stages_gated = [c[1] for c in gb.created]
    assert "p0" in stages_gated and "p1" in stages_gated
    p1_gate = next(c for c in gb.created if c[1] == "p1")
    assert any("profiling_summary.md" in r or r.endswith(".json") for r in p1_gate[2])

    # approve P1 → advances to p2 (stub from here)
    s = await rt.resume("run-real", "approve")
    st = await rt.get_state("run-real")
    assert st.values.get("stage_status", {}).get("p1") == "completed"
