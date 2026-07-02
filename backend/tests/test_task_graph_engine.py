"""R10 T5 tests: TaskGraph edge-strategy validator (§8).

Evidence for T5 (planning §5 V1/V6): all 10 edge types + 8 dimensions recognised;
parallel without merge_strategy → error; retry without retry_policy → error;
failure without failure_policy → error; gate without gate_policy → error;
out-of-vocab enum → error; optional dims get EXPLICIT defaults (not silent null,
RK-4); high-risk edge must require a Gate.
"""

from app.services.task_graph_service import (
    validate_edge_strategy, validate_edges,
    EDGE_TYPES, EDGE_DIMENSIONS, TRIGGER_CONDITIONS, MERGE_STRATEGIES,
    FAILURE_POLICIES, DEPENDENCIES, NA,
)


def _seq_edge(**over):
    e = {"edge_id": "e1", "edge_type": "sequence", "trigger_condition": "on_success",
         "dependency": "hard_dependency"}
    e.update(over)
    return e


def test_spec_constants_counts():
    assert len(EDGE_TYPES) == 10
    assert len(EDGE_DIMENSIONS) == 8
    assert len(TRIGGER_CONDITIONS) == 10
    assert len(MERGE_STRATEGIES) == 6
    assert len(FAILURE_POLICIES) == 6
    assert len(DEPENDENCIES) == 6


def test_valid_sequence_edge():
    res = validate_edge_strategy(_seq_edge())
    assert res.valid
    # optional dims filled with explicit defaults, not null (RK-4)
    assert res.normalized["merge_strategy"] == NA
    assert res.normalized["retry_policy"] == NA
    assert res.normalized["passing_policy"]["trace"] is True
    assert res.warnings  # defaults were applied explicitly → warnings recorded


def test_missing_edge_type_error():
    res = validate_edge_strategy({"edge_id": "e"})
    assert not res.valid
    assert any("edge_type" in e for e in res.errors)


def test_unknown_edge_type_error():
    res = validate_edge_strategy(_seq_edge(edge_type="teleport"))
    assert not res.valid
    assert any("10 种边类型" in e for e in res.errors)


def test_parallel_requires_merge_strategy():
    res = validate_edge_strategy(_seq_edge(edge_type="parallel", merge_strategy=None))
    assert not res.valid
    assert any("merge_strategy" in e for e in res.errors)
    # with a valid merge_strategy it passes
    ok = validate_edge_strategy(_seq_edge(edge_type="parallel", merge_strategy="all_success"))
    assert ok.valid


def test_retry_requires_retry_policy():
    res = validate_edge_strategy(_seq_edge(edge_type="retry"))
    assert not res.valid
    assert any("retry_policy" in e for e in res.errors)
    ok = validate_edge_strategy(_seq_edge(edge_type="retry",
                                          retry_policy={"max_retries": 3, "retry_on": ["timeout"]}))
    assert ok.valid


def test_retry_policy_missing_max_retries():
    res = validate_edge_strategy(_seq_edge(edge_type="retry", retry_policy={"retry_on": ["x"]}))
    assert not res.valid
    assert any("max_retries" in e for e in res.errors)


def test_failure_requires_failure_policy():
    res = validate_edge_strategy(_seq_edge(edge_type="failure"))
    assert not res.valid
    assert any("failure_policy" in e for e in res.errors)
    ok = validate_edge_strategy(_seq_edge(edge_type="failure", failure_policy="route_to_gate"))
    assert ok.valid


def test_conditional_requires_trigger_condition():
    res = validate_edge_strategy({"edge_id": "e", "edge_type": "conditional",
                                  "dependency": "hard_dependency"})
    assert not res.valid
    assert any("trigger_condition" in e for e in res.errors)


def test_gate_requires_gate_policy():
    res = validate_edge_strategy(_seq_edge(edge_type="gate"))
    assert not res.valid
    assert any("gate_policy" in e for e in res.errors)
    ok = validate_edge_strategy(_seq_edge(edge_type="gate",
                                          gate_policy={"gate_required": True, "gate_type": "task_gate"}))
    assert ok.valid


def test_high_risk_edge_must_require_gate():
    res = validate_edge_strategy(_seq_edge(risk_level="L5",
                                           gate_policy={"gate_required": False}))
    assert not res.valid
    assert any("高风险" in e for e in res.errors)
    ok = validate_edge_strategy(_seq_edge(risk_level="L5",
                                          gate_policy={"gate_required": True}))
    assert ok.valid


def test_out_of_vocab_enum_error():
    res = validate_edge_strategy(_seq_edge(dependency="whatever"))
    assert not res.valid
    assert any("dependency" in e for e in res.errors)
    res2 = validate_edge_strategy(_seq_edge(edge_type="failure", failure_policy="explode"))
    assert not res2.valid


def test_validate_edges_aggregate():
    edges = [
        _seq_edge(edge_id="e1"),
        _seq_edge(edge_id="e2", edge_type="parallel", merge_strategy="any_success"),
        _seq_edge(edge_id="e3", edge_type="retry"),  # invalid: no retry_policy
    ]
    result = validate_edges(edges)
    assert result.valid is False
    assert len(result.edges) == 3
    assert any("e3" in msg for msg in result.errors)
    # e1/e2 individually valid
    assert result.edges[0].valid and result.edges[1].valid
    assert result.edges[2].valid is False


# ─────────────────────────────────────────────────────────────────────────
# T6: TaskGraph scheduling engine
# ─────────────────────────────────────────────────────────────────────────
import pytest
from app.services.task_graph_service import TaskGraphEngine, TaskGraphRunResult


def _node(nid, **over):
    n = {"node_id": nid, "stage": "p3", "node_type": "planning",
         "acceptance_criteria": [f"crit-{nid}"], "permission_boundary": "workspace_read",
         "risk_level": "L0"}
    n.update(over)
    return n


def _edge(src, tgt, etype="sequence", **over):
    e = {"edge_id": f"{src}->{tgt}", "source_node_id": src, "target_node_id": tgt,
         "edge_type": etype, "trigger_condition": "on_success", "dependency": "hard_dependency"}
    e.update(over)
    return e


async def test_engine_sequence_all_completed():
    graph = {"nodes": [_node("n1"), _node("n2"), _node("n3")],
             "edges": [_edge("n1", "n2"), _edge("n2", "n3")]}
    res = await TaskGraphEngine().execute(graph, project_id="p")
    assert res.graph_status == "completed"
    assert set(res.completed_nodes) == {"n1", "n2", "n3"}
    # every node ran through NodeLoop → acceptance recorded
    for nid in ("n1", "n2", "n3"):
        assert res.node_results[nid]["acceptance"]["result"] == "accepted"


async def test_engine_parallel_group_merge():
    graph = {
        "nodes": [_node("n1"), _node("n2", parallel_group="g1"),
                  _node("n3", parallel_group="g1"), _node("n4")],
        "edges": [
            _edge("n1", "n2", "parallel", merge_strategy="all_success"),
            _edge("n1", "n3", "parallel", merge_strategy="all_success"),
            _edge("n2", "n4"), _edge("n3", "n4"),
        ],
    }
    res = await TaskGraphEngine().execute(graph, project_id="p")
    assert res.graph_status == "completed"
    assert set(res.completed_nodes) == {"n1", "n2", "n3", "n4"}


async def test_engine_invalid_edge_rejected():
    graph = {"nodes": [_node("n1"), _node("n2")],
             "edges": [_edge("n1", "n2", "parallel", merge_strategy=None)]}
    res = await TaskGraphEngine().execute(graph, project_id="p")
    assert res.graph_status == "failed"
    assert "edge strategy validation failed" in res.reason


async def _raising():
    raise RuntimeError("node worker crashed")


async def test_engine_failure_fail_fast():
    graph = {"nodes": [_node("n1"), _node("n2")],
             "edges": [_edge("n1", "n2", "failure", failure_policy="fail_fast")]}
    res = await TaskGraphEngine(node_loop=None).execute(
        graph, project_id="p", node_executors={"n1": _raising})
    assert res.graph_status == "failed"


async def test_engine_failure_route_to_gate():
    graph = {"nodes": [_node("n1"), _node("g")],
             "edges": [_edge("n1", "g", "failure", failure_policy="route_to_gate",
                             trigger_condition="on_failure")]}
    res = await TaskGraphEngine().execute(
        graph, project_id="p", node_executors={"n1": _raising})
    assert res.graph_status == "waiting_gate"
    assert "n1" in res.gated_nodes


async def test_engine_failure_mark_blocked():
    graph = {"nodes": [_node("n1"), _node("n2")],
             "edges": [_edge("n1", "n2", "failure", failure_policy="mark_blocked",
                             trigger_condition="on_failure")]}
    res = await TaskGraphEngine().execute(
        graph, project_id="p", node_executors={"n1": _raising})
    assert res.graph_status == "blocked"


async def test_engine_unhandled_failure_no_hidden_path():
    """Red line §6.2-8: a failed node with no failure edge → explicit graph failure."""
    graph = {"nodes": [_node("n1"), _node("n2")], "edges": [_edge("n1", "n2")]}
    res = await TaskGraphEngine().execute(
        graph, project_id="p", node_executors={"n1": _raising})
    assert res.graph_status == "failed"


async def test_engine_every_node_runs_nodeloop():
    graph = {"nodes": [_node("n1"), _node("n2")], "edges": [_edge("n1", "n2")]}
    res = await TaskGraphEngine().execute(graph, project_id="p")
    assert set(res.node_results.keys()) == {"n1", "n2"}
    for nid in ("n1", "n2"):
        # NodeLoop reached its final route step for each node
        assert res.node_results[nid]["step_reached"] == "route_decided"


def test_t7_engine_does_not_build_langgraph():
    """Red line (STOP-3/D-037): the engine must NOT build a LangGraph StateGraph.
    Matches actual calls (StateGraph(...) / .add_node(...) / .add_edge(...)), not
    the docstring that explains the prohibition."""
    import subprocess
    src = subprocess.run(
        ["grep", "-nE", r"StateGraph\(|\.add_node\(|\.add_edge\(|\.compile\(",
         "/home/king/rebuild/backend/app/services/task_graph_service.py"],
        capture_output=True, text=True)
    assert src.stdout.strip() == "", f"engine must not build main orchestration: {src.stdout}"
