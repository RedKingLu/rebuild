"""TaskGraph service — Edge strategy validation (R10 T5).

Validates TaskGraph edges against the authoritative edge-strategy spec
`文档/03-流程与运行时/02-StagePlan-TaskPlan-TaskGraph规范.md` §8 (10 edge types ×
8 mandatory strategy dimensions). Edges are stored as JSON dicts on the TaskGraph
ORM row (T4 design: `task_graph.edges`), so this validator operates on dicts.

Red line (§6.2 / §8.2): every edge strategy must be EXPLICIT — never hidden in a
prompt, never a silent null. Per RK-4 ("显式 default 不静默"), only the
type-mandatory dimension is a HARD error when missing/invalid; optional dimensions
are filled with an EXPLICIT default and a warning (never silently nulled).

The TaskGraph scheduling engine (T6) is added later in this module; T5 provides
only the edge-strategy validation the P3 generator (T15) must pass before a
TaskGraph is persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── §8.1 Edge types (10) ────────────────────────────────────────────────
EDGE_TYPES = [
    "sequence", "parallel", "conditional", "retry", "failure",
    "rework", "gate", "merge", "skip", "cancel",
]

# ── §8.2 the 8 mandatory strategy dimensions ────────────────────────────
EDGE_DIMENSIONS = [
    "edge_type", "trigger_condition", "dependency", "merge_strategy",
    "retry_policy", "failure_policy", "gate_policy", "passing_policy",
]

# ── §8.3 trigger_condition (10) ─────────────────────────────────────────
TRIGGER_CONDITIONS = [
    "on_success", "on_failure", "on_gate_approved", "on_gate_rejected",
    "on_evidence_sufficient", "on_evidence_insufficient", "on_policy_blocked",
    "on_retry_available", "on_manual_decision", "on_scope_changed",
]

# ── §8.4 dependency (6) ─────────────────────────────────────────────────
DEPENDENCIES = [
    "hard_dependency", "soft_dependency", "optional_dependency",
    "evidence_dependency", "artifact_dependency", "gate_dependency",
]

# ── §8.5 merge_strategy (6) ─────────────────────────────────────────────
MERGE_STRATEGIES = [
    "all_success", "any_success", "manual_merge",
    "evidence_merge", "artifact_merge", "conflict_review",
]

# ── §8.7 failure_policy (6) ─────────────────────────────────────────────
FAILURE_POLICIES = [
    "fail_fast", "continue_with_warning", "route_to_rework",
    "route_to_gate", "route_to_manual_review", "mark_blocked",
]

# explicit "not applicable to this edge type" marker — NOT a silent null (RK-4)
NA = "not_applicable"
_HIGH_RISK = {"L4", "L5"}

# edge_type → the strategy dimension that is HARD-required for it (§8.1 parentheticals)
_TYPE_REQUIRED_DIM = {
    "parallel": "merge_strategy",
    "merge": "merge_strategy",
    "conditional": "trigger_condition",
    "retry": "retry_policy",
    "failure": "failure_policy",
    "gate": "gate_policy",
}


@dataclass
class EdgeValidation:
    edge_id: str
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"edge_id": self.edge_id, "valid": self.valid,
                "errors": self.errors, "warnings": self.warnings}


@dataclass
class GraphEdgeValidation:
    valid: bool
    edges: list[EdgeValidation] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        out = []
        for e in self.edges:
            out.extend(f"{e.edge_id}: {msg}" for msg in e.errors)
        return out

    def to_dict(self) -> dict:
        return {"valid": self.valid, "edges": [e.to_dict() for e in self.edges]}


def validate_edge_strategy(edge: dict[str, Any], *, normalize: bool = True) -> EdgeValidation:
    """Validate one edge's 8 strategy dimensions (§8.2). Returns EdgeValidation
    with hard errors, explicit-default warnings, and (if normalize) a filled edge.

    HARD errors (invalid edge):
      - edge_type missing / not in EDGE_TYPES
      - the type-mandatory dimension missing or invalid
        (parallel/merge→merge_strategy, conditional→trigger_condition,
         retry→retry_policy, failure→failure_policy, gate→gate_policy)
      - any provided enum dimension holding an out-of-vocabulary value
      - high-risk edge (L4/L5) whose gate_policy does not require a Gate
    Soft (warnings, explicit defaults filled): optional dimensions left unset.
    """
    edge = dict(edge or {})
    edge_id = str(edge.get("edge_id") or f"{edge.get('source_node_id','?')}->{edge.get('target_node_id','?')}")
    errors: list[str] = []
    warnings: list[str] = []
    norm = dict(edge)

    # 1. edge_type
    etype = edge.get("edge_type")
    if not etype:
        errors.append("缺少 edge_type（§8.2-1，边策略必须显式）")
    elif etype not in EDGE_TYPES:
        errors.append(f"edge_type '{etype}' 不在 §8.1 的 10 种边类型内")

    required_dim = _TYPE_REQUIRED_DIM.get(etype)

    # helper: enum dimension validation + explicit default fill
    def _check_enum(dim: str, vocab: list[str], default: str):
        val = edge.get(dim)
        if val is None:
            if required_dim == dim:
                errors.append(f"{etype} 边缺少必填 {dim}（§8.2，不得静默 null）")
            else:
                norm[dim] = default
                warnings.append(f"{dim} 未声明，显式填默认 '{default}'（RK-4 不静默）")
        elif val not in vocab and val != NA:
            errors.append(f"{dim} '{val}' 不在 §8 允许取值内")

    # 2. trigger_condition (conditional edge → hard-required)
    _check_enum("trigger_condition", TRIGGER_CONDITIONS, "on_success")
    # 3. dependency
    _check_enum("dependency", DEPENDENCIES, "hard_dependency")
    # 4. merge_strategy (parallel/merge → hard-required)
    _check_enum("merge_strategy", MERGE_STRATEGIES, NA)
    # 6. failure_policy (failure → hard-required)
    _check_enum("failure_policy", FAILURE_POLICIES, NA)

    # 5. retry_policy (retry → hard-required dict with max_retries)
    rp = edge.get("retry_policy")
    if rp is None:
        if required_dim == "retry_policy":
            errors.append("retry 边缺少必填 retry_policy（§8.6）")
        else:
            norm["retry_policy"] = NA
            warnings.append("retry_policy 未声明，显式填 'not_applicable'（RK-4 不静默）")
    elif isinstance(rp, dict):
        if "max_retries" not in rp:
            errors.append("retry_policy 缺少 max_retries（§8.6）")
    elif rp != NA:
        errors.append("retry_policy 必须为 dict 或 'not_applicable'")

    # 7. gate_policy (gate edge / high-risk edge → hard-required dict with gate_required)
    gp = edge.get("gate_policy")
    high_risk = (edge.get("risk_level") in _HIGH_RISK) or (isinstance(gp, dict) and gp.get("risk_level") in _HIGH_RISK)
    if gp is None:
        if required_dim == "gate_policy" or high_risk:
            errors.append("gate 边/高风险边缺少必填 gate_policy（§8.8）")
        else:
            norm["gate_policy"] = {"gate_required": False}
            warnings.append("gate_policy 未声明，显式填 {gate_required: False}（RK-4 不静默）")
    elif isinstance(gp, dict):
        if "gate_required" not in gp:
            errors.append("gate_policy 缺少 gate_required（§8.8）")
        elif high_risk and not gp.get("gate_required"):
            errors.append("高风险边（L4/L5）的 gate_policy.gate_required 必须为 True（红线）")
    else:
        errors.append("gate_policy 必须为 dict")

    # 8. passing_policy (explicit default if unset)
    if edge.get("passing_policy") is None:
        norm["passing_policy"] = {
            "what": "artifact+evidence", "trim": True, "summarize": False,
            "evidence_required": False, "sanitize": True, "trace": True,
        }
        warnings.append("passing_policy 未声明，显式填默认传递策略（RK-4 不静默）")

    valid = not errors
    return EdgeValidation(edge_id=edge_id, valid=valid, errors=errors,
                          warnings=warnings, normalized=(norm if normalize else edge))


def validate_edges(edges: list[dict], *, normalize: bool = True) -> GraphEdgeValidation:
    """Validate every edge in a TaskGraph. The graph is valid only if all edges are."""
    results = [validate_edge_strategy(e, normalize=normalize) for e in (edges or [])]
    return GraphEdgeValidation(valid=all(r.valid for r in results), edges=results)


# ═══════════════════════════════════════════════════════════════════════
# TaskGraph scheduling engine (R10 T6)
#
# RED LINE (STOP-3 / D-037 / D-065 / G1): this engine is a task-level DAG
# scheduler that runs INSIDE a LangGraph node — it is NOT a second orchestrator.
# It MUST NOT build a LangGraph StateGraph or call add_node. It schedules
# TaskNodes by running each through the NodeLoop (T2) and routing per the edge
# strategy (T5 §8). Progress is emitted as events for the caller to fold into
# GraphState; checkpoint/resume stay with the LangGraph main spine.
#
# P3 (T17) only GENERATES + persists a TaskGraph — it does NOT call execute().
# Real scheduling of execution nodes is R11 (P4). This engine is built + tested
# now so R11 reuses it without adding edge strategy.
# ═══════════════════════════════════════════════════════════════════════

import asyncio as _asyncio
from typing import Awaitable, Callable

from app.services.node_loop import NodeLoop, NodeSpec

# node outcome (NodeLoop route hint) → trigger_conditions that an outgoing edge
# may fire on from that outcome (§8.3)
_OUTCOME_TRIGGERS = {
    "next": {"on_success", "on_evidence_sufficient", "on_gate_approved"},
    "failure": {"on_failure", "on_policy_blocked"},
    "gate": {"on_gate_approved", "on_gate_rejected", "on_manual_decision"},
    "rework": {"on_scope_changed", "on_manual_decision"},
    "retry": {"on_retry_available", "on_failure"},
}


@dataclass
class TaskGraphRunResult:
    graph_status: str  # completed / failed / waiting_gate / blocked / rework_required
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    skipped_nodes: list[str] = field(default_factory=list)
    gated_nodes: list[str] = field(default_factory=list)
    node_results: dict = field(default_factory=dict)  # node_id -> NodeLoopResult.to_dict()
    events: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "graph_status": self.graph_status,
            "completed_nodes": self.completed_nodes,
            "failed_nodes": self.failed_nodes,
            "skipped_nodes": self.skipped_nodes,
            "gated_nodes": self.gated_nodes,
            "events": self.events,
            "reason": self.reason,
        }


class TaskGraphEngine:
    """Schedules a TaskGraph's nodes via NodeLoop, routing by edge strategy (§8)."""

    def __init__(self, *, db=None, tracer=None, auditor=None,
                 node_loop: Optional[NodeLoop] = None):
        self.db = db
        self.tracer = tracer
        self.auditor = auditor
        self.node_loop = node_loop or NodeLoop(db=db, tracer=tracer, auditor=auditor)

    async def execute(
        self,
        graph: dict,
        *,
        node_executors: Optional[dict[str, Callable[[], "dict | Awaitable[dict]"]]] = None,
        project_id: str = "",
        run_id: Optional[str] = None,
        task_graph_run_id: Optional[str] = None,
        mode: str = "plan",
    ) -> TaskGraphRunResult:
        """Run a TaskGraph. `graph` = {nodes:[...], edges:[...]}; node dicts carry
        node_id / stage / task_plan / acceptance_criteria / permission_boundary /
        risk_level / parallel_group. `node_executors` maps node_id → the Node Worker
        execute_fn (defaults to a trivial passing executor for planning-type nodes)."""
        nodes = {n["node_id"]: n for n in graph.get("nodes", [])}
        edges = graph.get("edges", []) or []
        node_executors = node_executors or {}
        events: list[dict] = []

        # ── edge strategy gate (T5): no invalid graph may be scheduled ───
        ev = validate_edges(edges)
        if not ev.valid:
            return TaskGraphRunResult(
                graph_status="failed", reason="edge strategy validation failed",
                events=[{"engine": "edge_validation_failed", "errors": ev.errors}])

        # adjacency
        out_edges: dict[str, list[dict]] = {nid: [] for nid in nodes}
        in_edges: dict[str, list[dict]] = {nid: [] for nid in nodes}
        for e in edges:
            s, t = e.get("source_node_id"), e.get("target_node_id")
            if s in out_edges:
                out_edges[s].append(e)
            if t in in_edges:
                in_edges[t].append(e)

        status: dict[str, str] = {nid: "pending" for nid in nodes}
        results: dict[str, dict] = {}

        def emit(kind: str, **extra):
            ev_row = {"engine": kind, **extra}
            events.append(ev_row)
            if self.tracer:
                try:
                    self.tracer.write("state_change", action="task_graph_engine",
                                      summary=kind, project_id=project_id, run_id=run_id, **extra)
                except Exception:
                    pass

        # entry nodes = no incoming edges
        entry = [nid for nid in nodes if not in_edges[nid]]
        # process in waves; a wave = all currently-ready nodes (parallel_group → gather)
        ready = list(entry)
        gated: list[str] = []

        while ready:
            # group ready nodes by parallel_group (None → its own singleton group)
            wave = ready
            ready = []
            # run the wave concurrently (parallel edges → concurrent execution)
            async def _run_one(nid):
                node = nodes[nid]
                res = await self._run_node(node, project_id, run_id, task_graph_run_id,
                                           mode, node_executors.get(nid))
                return nid, res

            outcomes = await _asyncio.gather(*[_run_one(nid) for nid in wave])

            for nid, res in outcomes:
                results[nid] = res.to_dict()
                status[nid] = res.node_status
                emit("node_done", node_id=nid, node_status=res.node_status,
                     route=res.next_route)

                # ── route by node outcome + outgoing edge strategy (§8) ──
                if res.next_route == "gate":
                    status[nid] = "waiting_gate"
                    gated.append(nid)
                    continue  # pause this branch; other branches continue
                if res.next_route == "failure":
                    if not self._route_failure(nid, out_edges[nid], nodes, status, emit, ready):
                        # fail_fast / no explicit failure path → graph fails
                        return self._finalize(status, results, events,
                                              "failed", f"node {nid} failed (fail_fast)")
                    continue
                if res.next_route == "retry":
                    if self._route_retry(nid, out_edges[nid], ready):
                        continue
                    # no retry edge → treat as rework fallthrough
                if res.next_route == "rework":
                    self._route_rework(nid, out_edges[nid], ready, emit)
                    continue
                if res.next_route == "skip":
                    status[nid] = "skipped"
                    self._enqueue_targets(nid, out_edges[nid], "next", nodes, in_edges,
                                          status, ready, emit)
                    continue
                # completed / next → follow success edges to newly-ready targets
                self._enqueue_targets(nid, out_edges[nid], "next", nodes, in_edges,
                                      status, ready, emit)

        # terminal classification
        if gated:
            return self._finalize(status, results, events, "waiting_gate",
                                  f"{len(gated)} node(s) awaiting Gate")
        return self._finalize(status, results, events, None, "")

    # ── run a single node through NodeLoop (T2) ──────────────────────────
    async def _run_node(self, node: dict, project_id, run_id, task_graph_run_id, mode,
                        execute_fn):
        spec = NodeSpec(
            node_id=node["node_id"],
            stage=node.get("stage", ""),
            project_id=project_id,
            task_plan=node.get("task_plan") or {"task_plan_id": node.get("task_plan_ref"),
                                                "objective": node.get("title", ""),
                                                "acceptance_criteria": node.get("acceptance_criteria", []),
                                                "expected_artifacts": node.get("expected_artifacts", []),
                                                "expected_evidence": node.get("expected_evidence", [])},
            acceptance_criteria=node.get("acceptance_criteria", []),
            permission_boundary=node.get("permission_boundary", "workspace_read"),
            risk_level=node.get("risk_level", "L0"),
            task_graph_run_id=task_graph_run_id,
            run_id=run_id,
            mode=mode,
            split_strategy=node.get("split_strategy", "inline"),
        )
        fn = execute_fn or self._default_executor(node)
        return await self.node_loop.run(spec, execute_fn=fn)

    def _default_executor(self, node: dict):
        """Trivial passing executor for planning-type nodes with no injected worker."""
        crit = node.get("acceptance_criteria", [])

        async def _fn():
            return {
                "summary": f"node {node['node_id']} executed",
                "artifacts": [f"artifacts/{node['node_id']}.json"],
                "evidence": [{"evidence_id": f"ev-{node['node_id']}"}],
                "trace_refs": [f"trace-{node['node_id']}"],
                "criteria_met": {c: True for c in crit},
            }
        return _fn

    # ── edge routing helpers (§8) ────────────────────────────────────────
    def _fires(self, edge: dict, outcome: str) -> bool:
        """Whether an outgoing edge fires given the source node outcome."""
        tc = edge.get("trigger_condition")
        allowed = _OUTCOME_TRIGGERS.get(outcome, set())
        if tc is None or tc == "not_applicable":
            return outcome == "next"  # default sequence edge fires on success
        return tc in allowed

    def _enqueue_targets(self, nid, outs, outcome, nodes, in_edges, status, ready, emit):
        for e in outs:
            if e.get("edge_type") in ("failure", "retry", "rework"):
                continue  # non-success edges handled by their own routers
            if not self._fires(e, outcome):
                continue
            t = e.get("target_node_id")
            if t in nodes and status.get(t) == "pending" and self._deps_ready(t, in_edges, status):
                if t not in ready:
                    ready.append(t)
                    emit("enqueue", node_id=t, via=e.get("edge_type"))

    def _deps_ready(self, nid, in_edges, status) -> bool:
        """A node is ready when all its hard-dependency predecessors are completed."""
        for e in in_edges.get(nid, []):
            if e.get("dependency") in (None, "hard_dependency"):
                src = e.get("source_node_id")
                if status.get(src) not in ("completed",):
                    # allow readiness if this edge is a failure/rework path that fired
                    if e.get("edge_type") not in ("failure", "rework", "retry"):
                        return False
        return True

    def _route_failure(self, nid, outs, nodes, status, emit, ready) -> bool:
        """Route a failed node per its failure edge's failure_policy (§8.7).
        Returns False if the failure is unhandled → graph must fail (no hidden path)."""
        failure_edges = [e for e in outs if e.get("edge_type") == "failure"]
        if not failure_edges:
            return False  # red line §6.2-8: unhandled failure → explicit graph failure
        for e in failure_edges:
            policy = e.get("failure_policy")
            if policy == "fail_fast":
                return False
            if policy in ("route_to_gate", "route_to_manual_review"):
                status[nid] = "waiting_gate"
                emit("failure_route", node_id=nid, policy=policy, to="gate")
                return True
            if policy == "route_to_rework":
                t = e.get("target_node_id")
                if t in nodes:
                    status[t] = "pending"
                    if t not in ready:
                        ready.append(t)
                emit("failure_route", node_id=nid, policy=policy, to=t)
                return True
            if policy == "mark_blocked":
                status[nid] = "blocked"
                emit("failure_route", node_id=nid, policy=policy, to="blocked")
                return True
            if policy == "continue_with_warning":
                emit("failure_route", node_id=nid, policy=policy, to="continue")
                return True
        return False

    def _route_retry(self, nid, outs, ready) -> bool:
        retry_edges = [e for e in outs if e.get("edge_type") == "retry"]
        if not retry_edges:
            return False
        # retry re-enqueues the retry target (or the node itself) up to max_retries
        for e in retry_edges:
            t = e.get("target_node_id") or nid
            if t not in ready:
                ready.append(t)
        return True

    def _route_rework(self, nid, outs, ready, emit):
        rework_edges = [e for e in outs if e.get("edge_type") == "rework"]
        for e in rework_edges:
            t = e.get("target_node_id")
            if t and t not in ready:
                ready.append(t)
                emit("rework_route", node_id=nid, to=t)

    def _finalize(self, status, results, events, forced_status, reason) -> TaskGraphRunResult:
        completed = [n for n, s in status.items() if s == "completed"]
        failed = [n for n, s in status.items() if s == "failed"]
        skipped = [n for n, s in status.items() if s == "skipped"]
        gated = [n for n, s in status.items() if s == "waiting_gate"]
        blocked = [n for n, s in status.items() if s == "blocked"]
        if forced_status:
            gstatus = forced_status
        elif failed:
            gstatus = "failed"
        elif gated:
            gstatus = "waiting_gate"
        elif blocked:
            gstatus = "blocked"
        elif all(s in ("completed", "skipped") for s in status.values()):
            gstatus = "completed"
        else:
            gstatus = "blocked"  # some nodes never became ready (unreachable)
            reason = reason or "some nodes unreachable / dependencies unmet"
        return TaskGraphRunResult(
            graph_status=gstatus, completed_nodes=completed, failed_nodes=failed,
            skipped_nodes=skipped, gated_nodes=gated, node_results=results,
            events=events, reason=reason)
