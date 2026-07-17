"""R9-5-1 spine tests — real LangGraph orchestration: interrupt / resume / checkpoint.

Proves the orchestration red line (G1): P0-P6 flow runs through a compiled StateGraph,
promotion Gates pause via interrupt and resume from checkpoint, conditional routing
(approve / reject / request_changes) works, and reducers accumulate state.

Uses fake stage handlers + an in-memory gate backend so the spine is testable without
a DB/project. Real P0/P1 handler wiring + route delegation is 阶段 C (T10/T11).
"""

import pytest

from app.core.config import settings
from app.graph import nodes
from app.graph.checkpoint import reset_checkpointer_for_test
from app.graph.runtime import get_flow_runtime, reset_flow_runtime_for_test
from app.services.review_pass import ReviewResult


class FakeHandler:
    goal = "fake stage"
    acceptance_criteria = ["c1"]
    planned_actions = ["a1"]

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def execute(self, state):
        self.calls += 1
        # A completed stage must have a real domain artifact + Evidence (D-066); register
        # them so the independent AcceptanceService gate (R17.3-6 WP-2 批B返工 C4) sees a
        # legitimate completion rather than an evidence-less fake. Best-effort (advisory).
        # Stage is the in_progress one (current_stage is only set on the node's return).
        ss = state.get("stage_status") or {}
        stage = next((k for k, v in ss.items() if v == "in_progress"),
                     state.get("current_stage", "p0"))
        pid = state.get("project_id", "")
        try:
            from app.services import workspace_service
            art = workspace_service.workspace_path(pid) / "artifacts"
            art.mkdir(parents=True, exist_ok=True)
            (art / f"{stage}_domain.json").write_text('{"ok": true}', encoding="utf-8")
            from app.services.aet_service import AETService
            AETService(None).write_evidence(
                pid, f"ev-fake-{stage}", evidence_type="fake_domain", status="candidate",
                source="fake_handler", claim="fake completion evidence", stage=stage)
        except Exception:
            pass
        return {"status": "completed" if not self.fail else "blocked",
                "artifacts": [f"{state.get('current_stage','x')}_out.json"],
                "ok": not self.fail}

    def review(self, result):
        # 独立 ValidationAgent 从盘重读的 disk_view 只带控制信号（status）与域产物，不带
        # 进程内 "ok"；失败经 status=blocked 传导（declared!=completed → 独立验收不通过）。
        ok = result.get("status", "completed") == "completed" and bool(result.get("ok", True))
        return ReviewResult(passed=ok, issues=[] if ok else [{"e": "bad"}])


class FakeGate:
    def __init__(self):
        self.created = []
        self.decided = []
        self._n = 0

    def create(self, *, project_id, run_id, stage, artifact_refs,
               gate_type="stage_promotion", metadata=None):
        self._n += 1
        gid = f"gate-{stage}-{self._n}"
        self.created.append((gid, stage, list(artifact_refs)))
        return gid

    def decide(self, *, gate_id, decision):
        self.decided.append((gate_id, decision))


@pytest.fixture
async def spine(tmp_path, monkeypatch):
    # Settings is frozen — redirect the path-reading functions instead of attrs.
    ckpt = tmp_path / "graph_checkpoints.sqlite"
    monkeypatch.setattr("app.graph.checkpoint.checkpoint_path", lambda: ckpt)
    ws_root = tmp_path / "ws"
    monkeypatch.setattr("app.services.workspace_service._workspace_root", lambda: ws_root)
    nodes.clear_handlers()
    gb = FakeGate()
    nodes.set_gate_backend(gb)
    nodes.set_tracer_auditor(None, None)
    yield gb
    # Teardown: close the cached aiosqlite connection so the process can exit
    # (otherwise the lingering loop-bound connection hangs pytest at exit).
    await reset_checkpointer_for_test()
    reset_flow_runtime_for_test()
    nodes.clear_handlers()
    nodes.set_gate_backend(None)


async def _afresh():
    await reset_checkpointer_for_test()
    reset_flow_runtime_for_test()


def _init(run_id):
    return {"run_id": run_id, "project_id": "proj-test", "run_goal": "g",
            "run_status": "running", "stage_status": {"p0": "in_progress"}}


async def test_full_walk_approve_to_end(spine):
    await _afresh()
    nodes.register_handler("p0", FakeHandler())
    nodes.register_handler("p1", FakeHandler())
    rt = get_flow_runtime()

    s = await rt.start("run-walk", _init("run-walk"))
    assert "__interrupt__" in s, "should pause at p0 promotion Gate"
    assert rt.capability_status() == "live"

    interrupts = 0
    while "__interrupt__" in s and interrupts < 20:
        s = await rt.resume("run-walk", "approve")
        interrupts += 1

    assert interrupts == 7, "p0..p6 each pause at a promotion Gate"
    st = await rt.get_state("run-walk")
    vals = st.values
    assert vals.get("run_status") == "completed"
    assert vals.get("stage_status", {}).get("p6") == "completed"
    # only p0/p1 have real handlers → real gates created; p2-p6 are stubs
    assert len(spine.created) == 2
    assert [c[1] for c in spine.created] == ["p0", "p1"]
    assert spine.decided[:2] == [("gate-p0-1", "approve"), ("gate-p1-2", "approve")]
    # reducers accumulated events across stages and artifacts from p0/p1 reports
    assert len(vals.get("events", [])) >= 7
    assert len(vals.get("artifacts", [])) >= 2


async def test_checkpoint_persists_across_runtime_reset(spine):
    await _afresh()
    nodes.register_handler("p0", FakeHandler())
    rt = get_flow_runtime()
    s = await rt.start("run-ckpt", _init("run-ckpt"))
    assert "__interrupt__" in s

    # New runtime instance, same checkpointer (not reset) → state restored from disk
    reset_flow_runtime_for_test()
    rt2 = get_flow_runtime()
    st = await rt2.get_state("run-ckpt")
    assert st.values.get("project_id") == "proj-test"
    assert st.values.get("pending_gate", {}).get("stage") == "p0"
    # resume on the fresh runtime continues from the checkpoint
    s2 = await rt2.resume("run-ckpt", "approve")
    assert st.values.get("stage_status", {}).get("p0") in ("in_progress", "waiting_gate")


async def test_request_changes_reruns_stage(spine):
    await _afresh()
    h0 = FakeHandler()
    nodes.register_handler("p0", h0)
    rt = get_flow_runtime()
    await rt.start("run-rc", _init("run-rc"))
    assert h0.calls == 1
    s = await rt.resume("run-rc", "request_changes")
    assert "__interrupt__" in s, "rework returns to p0 gate"
    assert h0.calls == 2, "p0 work re-ran on request_changes"


async def test_reject_blocks_run(spine):
    await _afresh()
    nodes.register_handler("p0", FakeHandler())
    rt = get_flow_runtime()
    await rt.start("run-rej", _init("run-rej"))
    s = await rt.resume("run-rej", "reject")
    st = await rt.get_state("run-rej")
    assert st.values.get("run_status") == "blocked"
    assert st.values.get("stage_status", {}).get("p0") == "blocked"
    assert "__interrupt__" not in s


async def test_escalation_when_review_fails(spine):
    await _afresh()
    nodes.register_handler("p0", FakeHandler(fail=True))
    rt = get_flow_runtime()
    s = await rt.start("run-esc", _init("run-esc"))
    assert "__interrupt__" in s, "escalated stage still pauses at a Gate"
    st = await rt.get_state("run-esc")
    pg = st.values.get("pending_gate", {})
    assert pg.get("escalated") is True
