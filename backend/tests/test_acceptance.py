"""R10 T3 tests: AcceptanceService (independent acceptance executor).

Evidence for T3 (planning §5 V1/V5): the 8 checks (§Step8) run and are traceable,
the 8 routing results (§Step9) are reachable, missing Evidence → rework_required
(D-066), boundary/policy violation → gate_required (never approved, §7-4), and the
executor is independent of the node worker (D-082).
"""

from app.core.status import ACCEPTANCE_RESULTS
from app.services.acceptance_service import AcceptanceService, AcceptanceResult


def _full_package(**over):
    """A node package that should pass all 8 checks unless overridden."""
    pkg = {
        "node_status": "self_checking",
        "artifacts": ["artifacts/report.json"],
        "evidence": [{"evidence_id": "ev-1", "status": "candidate"}],
        "trace_refs": ["trace-1"],
        "audit_refs": [],
        "criteria_met": {"crit-a": True, "crit-b": True},
        "risk_level": "L1",
    }
    pkg.update(over)
    return pkg


def _plan(**over):
    p = {
        "acceptance_criteria": ["crit-a", "crit-b"],
        "expected_artifacts": ["report"],
        "expected_evidence": ["ev"],
        "risk_level": "L1",
    }
    p.update(over)
    return p


def test_all_pass_accepted():
    svc = AcceptanceService()
    res = svc.accept(_full_package(), _plan(), ["crit-a", "crit-b"])
    assert isinstance(res, AcceptanceResult)
    assert res.result == "accepted"
    # 8 checks recorded + traceable (§7-5)
    assert len(res.checks) == 8
    assert all(c.item and isinstance(c.passed, bool) for c in res.checks)


def test_result_always_in_enum():
    svc = AcceptanceService()
    for pkg in (_full_package(), _full_package(evidence=[]), {},
                _full_package(boundary_violation=True),
                _full_package(node_status="blocked")):
        res = svc.accept(pkg, _plan())
        assert res.result in ACCEPTANCE_RESULTS


def test_missing_evidence_rework_required():
    """D-066: missing Evidence cannot be marked completed → rework_required."""
    svc = AcceptanceService()
    res = svc.accept(_full_package(evidence=[]), _plan())
    assert res.result == "rework_required"
    ev_check = next(c for c in res.checks if "Evidence" in c.item)
    assert ev_check.passed is False


def test_missing_artifact_rework_required():
    svc = AcceptanceService()
    res = svc.accept(_full_package(artifacts=[]), _plan())
    assert res.result == "rework_required"


def test_boundary_violation_gate_required_never_approved():
    """§7-4: boundary violation must escalate to Gate, never approved."""
    svc = AcceptanceService()
    res = svc.accept(_full_package(boundary_violation=True), _plan())
    assert res.result == "gate_required"
    assert res.result not in ("accepted", "accepted_with_warning")


def test_policy_forbidden_gate_required():
    svc = AcceptanceService()
    res = svc.accept(_full_package(policy_forbidden=True), _plan())
    assert res.result == "gate_required"


def test_criteria_not_met_rework():
    svc = AcceptanceService()
    res = svc.accept(_full_package(criteria_met={"crit-a": True, "crit-b": False}), _plan())
    assert res.result == "rework_required"


def test_missing_trace_accepted_with_warning():
    svc = AcceptanceService()
    res = svc.accept(_full_package(trace_refs=[]), _plan())
    assert res.result == "accepted_with_warning"
    assert res.recommendations  # warning carries a recommendation


def test_high_risk_missing_audit_warning():
    """L4/L5 without Audit → not clean-accepted (audit check fails → warning)."""
    svc = AcceptanceService()
    res = svc.accept(_full_package(risk_level="L5", audit_refs=[]), _plan(risk_level="L5"))
    assert res.result == "accepted_with_warning"
    audit_check = next(c for c in res.checks if "Audit" in c.item)
    assert audit_check.passed is False


def test_blocked_and_skipped_short_circuit():
    svc = AcceptanceService()
    assert svc.accept(_full_package(node_status="blocked"), _plan()).result == "blocked"
    res = svc.accept(_full_package(node_status="skipped"), _plan())
    assert res.result == "skipped"
    assert "completed" in res.reason  # 不得伪装 completed


def test_independence_uses_acceptance_agent(client):
    """D-082: with a seeded DB, the executor stamps the Acceptance Agent id."""
    from app.core.database import get_session
    from app.models.agent_definition import AgentDefinition, AgentType
    db = get_session()
    try:
        svc = AcceptanceService(db=db)
        res = svc.accept(_full_package(), _plan(), ["crit-a", "crit-b"])
        agent = (db.query(AgentDefinition)
                 .filter(AgentDefinition.agent_type == AgentType.acceptance).first())
        # seed provides an Acceptance Agent → executor stamps its id (traceable independence)
        if agent is not None:
            assert res.agent_id == agent.agent_id
    finally:
        db.close()
