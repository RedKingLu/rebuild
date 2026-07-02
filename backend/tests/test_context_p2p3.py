"""R10 T7 tests: context_assembler P2/P3 adaptation.

Evidence for T7 (planning §5 / R10-2 组1): the canonical context assembler already
supports P2/P3 generically — select_agent maps p2/p3, SkillCategory has p2/p3,
node_state feeds the C5 layer, task_type falls back cleanly. T7 verifies P2/P3
context assembly works end to end (the NodeLoop Step-2 call path) and locks it
with a regression test — no bespoke per-stage code needed.
"""

import pytest

from app.services.context_assembler import assemble_context, build_system_prompt


@pytest.fixture
def pid(client):
    r = client.post("/api/projects", json={
        "name": "T7 ctx", "source_type": "manual", "source_config": {}})
    return r.json()["data"]["project_id"]


def test_p2_context_assembles(client, pid):
    ctx = assemble_context(
        pid, "p2",
        node_state={"node_task": "P2 风险评估", "task": "评估可行性"},
        task_type="assessment",
    )
    assert ctx["current_stage"] == "p2"
    assert ctx.get("selected_agent")  # an agent was selected for p2
    assert "layers" in ctx and ctx["layers"]
    # C5 (node task context) reflects the node_state we passed
    # (assemble_c5 uses node_state["task"] or ["node_task"]; "task" wins)
    if "C5" in ctx["layers"]:
        c5 = ctx["layers"]["C5"]
        assert "评估可行性" in str(c5)
    # assembly trace records what was assembled (Evidence-grade)
    assert ctx["assembly_trace"]["layers_assembled"]


def test_p3_context_assembles(client, pid):
    ctx = assemble_context(
        pid, "p3",
        node_state={"node_task": "P3 方案与 TaskGraph 生成"},
        task_type="planning",
    )
    assert ctx["current_stage"] == "p3"
    assert ctx.get("selected_agent")
    assert ctx["layers"]
    assert ctx["assembly_trace"]["selected_agent"]


def test_p2_p3_system_prompt_builds(client, pid):
    """NodeLoop / handler path: a system prompt string builds for p2 and p3."""
    for stage in ("p2", "p3"):
        sp = build_system_prompt(pid, stage,
                                 node_state={"node_task": f"{stage} task"},
                                 task_type="default")
        assert isinstance(sp, str) and len(sp) > 0


def test_p2_p3_stage_isolation(client, pid):
    """p2 and p3 assemblies are stage-scoped (different current_stage), not p0/p1."""
    c2 = assemble_context(pid, "p2", node_state={"node_task": "a"})
    c3 = assemble_context(pid, "p3", node_state={"node_task": "b"})
    assert c2["current_stage"] == "p2" and c3["current_stage"] == "p3"
