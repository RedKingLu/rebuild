"""R9-5-7 tests — execution-mode single source + Manual HITL + three-mode + T14/T15.

Covers:
  V1 unit       — mode_policy.authorize_action three-mode (9 combos) + risk map
  V1 service    — workspace_service.set/get_execution_mode round-trip
  V2/V3 API     — onboarding writes workspace.json mode; switch_mode/get_mode同源
  V1 HITL       — agent_loop parks controlled action behind action_approval Gate
                  (gate.request emitted, tool NOT executed) in Manual; auto executes
  V6 negative   — action_approval Gate decide does NOT advance stage; Auto cannot
                  bypass stage_promotion Gate (T15)
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.mode_policy import authorize_action, risk_for_action


# ── V1 unit: three-mode authorization (9 combos) ──────────────────────────

class TestThreeModeAuthorization:
    """D-025 / D-087②: manual / plan / auto behavioral difference."""

    def test_manual_requires_confirmation_for_controlled(self):
        for risk in ("L2", "L3"):
            assert authorize_action("manual", risk)["decision"] == "require_confirmation"

    def test_manual_confirmed_approves(self):
        assert authorize_action("manual", "L2", confirmed=True)["decision"] == "approved"

    def test_plan_passes_low_risk_confirms_l3(self):
        assert authorize_action("plan", "L1")["decision"] == "auto_approved"
        assert authorize_action("plan", "L2")["decision"] == "auto_approved"
        assert authorize_action("plan", "L3")["decision"] == "require_confirmation"

    def test_plan_in_plan_passes_l3(self):
        assert authorize_action("plan", "L3", in_plan=True)["decision"] == "auto_approved"

    def test_auto_passes_through_l3_confirms_high(self):
        for risk in ("L0", "L1", "L2", "L3"):
            assert authorize_action("auto", risk)["decision"] == "auto_approved"
        for risk in ("L4", "L5"):
            assert authorize_action("auto", risk)["decision"] == "require_confirmation"

    def test_high_risk_never_auto_in_any_mode(self):
        # L4+ is never auto-approved regardless of mode (HIGH_RISK_FLOOR).
        for mode in ("manual", "plan", "auto"):
            assert authorize_action(mode, "L5")["decision"] == "require_confirmation"

    def test_nine_combo_matrix(self):
        expected = {
            ("manual", "L1"): "require_confirmation",
            ("manual", "L3"): "require_confirmation",
            ("manual", "L5"): "require_confirmation",
            ("plan", "L1"): "auto_approved",
            ("plan", "L3"): "require_confirmation",
            ("plan", "L5"): "require_confirmation",
            ("auto", "L1"): "auto_approved",
            ("auto", "L3"): "auto_approved",
            ("auto", "L5"): "require_confirmation",
        }
        for (mode, risk), want in expected.items():
            assert authorize_action(mode, risk)["decision"] == want, (mode, risk)


# ── V1 unit: action→risk single-source map (Q-5) ──────────────────────────

class TestRiskMap:
    def test_known_actions(self):
        assert risk_for_action("read_artifact") == "L1"
        assert risk_for_action("run_profiling") == "L3"
        assert risk_for_action("execute_command") == "L4"

    def test_unknown_action_default_l2(self):
        assert risk_for_action("totally_unknown_tool") == "L2"


# ── V1 service: execution_mode single source ──────────────────────────────

class TestExecutionModeSingleSource:
    def test_set_get_round_trip(self, isolated_data):
        from app.services.workspace_service import set_execution_mode, get_execution_mode, init_workspace
        init_workspace("proj-mode-1")
        assert set_execution_mode("proj-mode-1", "manual") == "manual"
        assert get_execution_mode("proj-mode-1") == "manual"

    def test_invalid_mode_falls_back(self, isolated_data):
        from app.services.workspace_service import set_execution_mode, get_execution_mode, init_workspace
        init_workspace("proj-mode-2")
        set_execution_mode("proj-mode-2", "auto")
        # invalid value must not corrupt the source
        assert set_execution_mode("proj-mode-2", "bogus") == "auto"
        assert get_execution_mode("proj-mode-2") == "auto"


# ── V2/V3 API: onboarding mode + top-bar same source ──────────────────────

def _make_project(client) -> str:
    resp = client.post("/api/projects", json={
        "name": "HITL Test", "source_type": "manual", "source_config": {},
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["data"]["project_id"]


class TestOnboardingModeSameSource:
    def test_onboarding_writes_workspace_mode(self, client):
        pid = _make_project(client)
        resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={
            "execution_mode": "manual", "env_kind": "local",
        })
        assert resp.status_code == 200, resp.text
        # get_mode reads the SAME workspace.json execution_mode the wizard wrote
        mode = client.get(f"/api/projects/{pid}/mode").json()["data"]["execution_mode"]
        assert mode == "manual"

    def test_switch_mode_then_get_consistent(self, client):
        pid = _make_project(client)
        client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "plan"})
        client.put(f"/api/projects/{pid}/mode", json={"mode": "auto"})
        mode = client.get(f"/api/projects/{pid}/mode").json()["data"]["execution_mode"]
        assert mode == "auto"


# ── V1 HITL: agent_loop parks controlled action behind a Gate ─────────────

class _FakeFn:
    def __init__(self, name, args):
        self.name = name
        self.arguments = args


class _FakeToolCall:
    def __init__(self, idx, call_id, name, args):
        self.index = idx
        self.id = call_id
        self.function = _FakeFn(name, args)


class _FakeGateway:
    """Emits one tool call on the first round, then plain text after approval."""
    def __init__(self, tool_name):
        self.tool_name = tool_name
        self.rounds = 0

    async def call_stream(self, **kwargs):
        self.rounds += 1
        if self.rounds == 1:
            yield {"type": "tool_calls",
                   "tool_calls": [_FakeToolCall(0, "call_1", self.tool_name, "{}")]}
            yield {"type": "done"}
        else:
            yield {"type": "token", "content": "执行完成"}
            yield {"type": "done"}


def _run_chat(loop, **kwargs):
    async def _collect():
        frames = []
        async for f in loop.run(**kwargs):
            frames.append(f)
        return frames
    return asyncio.run(_collect())


class TestManualHITLChat:
    def _patch_services(self, monkeypatch, tool_name, created):
        from app.services import agent_loop as al_mod

        gateway = _FakeGateway(tool_name)

        def _fake_create(**kw):
            created.append(kw)
            return SimpleNamespace(gate_id="gate-action-1")

        fake_services = SimpleNamespace(
            model_gateway=gateway,
            gate_service=SimpleNamespace(create=_fake_create),
        )
        monkeypatch.setattr("app.dependencies.get_services", lambda: fake_services)
        return gateway

    def test_manual_parks_controlled_tool_behind_gate(self, isolated_data, monkeypatch):
        from app.services.agent_loop import AgentLoop
        created: list = []
        self._patch_services(monkeypatch, "run_profiling", created)  # L3 controlled

        loop = AgentLoop()
        executed: list = []

        async def _fake_exec(name, *a, **k):
            executed.append(name)
            return {"ok": True}
        monkeypatch.setattr(loop, "_execute_tool", _fake_exec)

        frames = _run_chat(loop, message="profile it", project_id="p-hitl",
                           project_name="X", stage="p1", mode="manual", run_id="r1")
        joined = "".join(frames)
        assert "event: gate.request" in joined          # action parked, surfaced
        assert executed == []                            # tool NOT executed (Manual)
        assert created and created[0]["gate_type"] == "action_approval"

    def test_auto_executes_low_risk_without_gate(self, isolated_data, monkeypatch):
        from app.services.agent_loop import AgentLoop
        created: list = []
        self._patch_services(monkeypatch, "run_profiling", created)  # L3 → auto passes

        loop = AgentLoop()
        executed: list = []

        async def _fake_exec(name, *a, **k):
            executed.append(name)
            return {"ok": True}
        monkeypatch.setattr(loop, "_execute_tool", _fake_exec)

        frames = _run_chat(loop, message="profile it", project_id="p-hitl",
                           project_name="X", stage="p1", mode="auto", run_id="r1")
        joined = "".join(frames)
        assert "event: gate.request" not in joined
        assert executed == ["run_profiling"]             # auto-executed

    def test_manual_confirmed_executes(self, isolated_data, monkeypatch):
        from app.services.agent_loop import AgentLoop
        created: list = []
        self._patch_services(monkeypatch, "run_profiling", created)

        loop = AgentLoop()
        executed: list = []

        async def _fake_exec(name, *a, **k):
            executed.append(name)
            return {"ok": True}
        monkeypatch.setattr(loop, "_execute_tool", _fake_exec)

        # confirm=True (post-approval resume) → controlled tool executes
        frames = _run_chat(loop, message="profile it", project_id="p-hitl",
                           project_name="X", stage="p1", mode="manual", run_id="r1",
                           confirm=True)
        joined = "".join(frames)
        assert "event: gate.request" not in joined
        assert executed == ["run_profiling"]


# ── V6 negative: action gate non-promotion + Auto cannot bypass promotion ──

class TestGateSemantics:
    def test_action_gate_decide_does_not_advance_stage(self, client):
        pid = _make_project(client)
        client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "manual"})
        # create an action_approval gate directly
        g = client.post(f"/api/projects/{pid}/gates", json={
            "gate_type": "action_approval", "stage": "p0", "risk_level": "L3",
            "summary": "action", "options": ["approve", "reject"],
        }).json()["data"]
        before = client.get(f"/api/projects/{pid}").json()["data"].get("current_stage")
        # decide approve
        resp = client.post(f"/api/projects/{pid}/gates/{g['gate_id']}/decision",
                           json={"decision": "approve", "reason": "ok"})
        assert resp.status_code == 200
        after = client.get(f"/api/projects/{pid}").json()["data"].get("current_stage")
        # action_approval is NOT stage_promotion → stage unchanged (公理6: no promotion)
        assert before == after

    def test_auto_mode_does_not_auto_resolve_stage_promotion(self, client):
        """T15: Auto mode must NOT bypass a stage_promotion Gate — it stays
        waiting_decision until an explicit user decision arrives."""
        pid = _make_project(client)
        client.post(f"/api/projects/{pid}/onboarding/complete", json={"execution_mode": "auto"})
        run_id = client.get(f"/api/projects/{pid}").json()["data"].get("current_run_id") or ""
        g = client.post(
            f"/api/projects/{pid}/runs/{run_id}/stages/p0/promotion-gate",
            json={"target_stage": "p1"},
        ).json()["data"]
        # Immediately read it back — Auto did not auto-approve it.
        fetched = client.get(f"/api/projects/{pid}/gates/active").json()["data"]
        assert fetched is not None
        assert fetched["gate_id"] == g["gate_id"]
        assert fetched["gate_status"] == "waiting_decision"
        assert fetched["gate_type"] == "stage_promotion"
