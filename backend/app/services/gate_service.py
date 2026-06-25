"""Gate service — CRUD and mock decision with mandatory Audit writing.

R4: Gate decisions MUST write an Audit entry (D-034 hard rule).
Policy Check and Risk Assessment are mock placeholders.
"""

import uuid
from typing import Optional

from app.repositories.fixtures import seed_gates
from app.schemas.gate import (
    GateResponse, GateDecisionRequest,
    PolicyCheckRequest, PolicyCheckResponse,
    RiskAssessmentRequest, RiskAssessmentResponse,
)
from app.schemas.aet import AuditResponse


class GateService:
    def __init__(self, services):
        self._svc = services
        self._gates: dict[str, GateResponse] = {}
        self._seed()

    def _seed(self):
        for g in seed_gates():
            self._gates[g.gate_id] = g

    def list_by_project(self, project_id: str) -> list[GateResponse]:
        return [g for g in self._gates.values() if g.project_id == project_id]

    def get(self, gate_id: str) -> Optional[GateResponse]:
        return self._gates.get(gate_id)

    def get_active(self, project_id: str) -> Optional[GateResponse]:
        for g in self._gates.values():
            if g.project_id == project_id and g.gate_status == "waiting_decision":
                return g
        return None

    def create(self, project_id: str, run_id: str, stage: str,
               gate_type: str, reason: str = "", risk_level: str = "L0",
               summary: str = "", options: list[str] | None = None) -> GateResponse:
        gid = f"gate-{uuid.uuid4().hex[:6]}"
        g = GateResponse(
            gate_id=gid,
            gate_type=gate_type,
            gate_status="created",
            project_id=project_id,
            run_id=run_id,
            stage=stage,
            reason=reason,
            risk_level=risk_level,
            summary=summary,
            options=options or ["approve", "reject"],
        )
        self._gates[gid] = g
        return g

    def decide(self, gate_id: str, req: GateDecisionRequest) -> tuple[Optional[GateResponse], Optional[dict]]:
        """Submit a Gate decision. Returns (updated_gate, audit).

        MUST write an Audit entry (D-034). This is enforced here, not optional.
        """
        g = self._gates.get(gate_id)
        if g is None:
            return None, None

        g.gate_status = "approved" if req.decision == "approve" else (
            "rejected" if req.decision == "reject" else "resolved"
        )
        g.decision = req.decision
        g.transition_mode = "mock"

        # MANDATORY: Write Audit entry (D-034, D-066)
        audit = self._svc.audit_writer.write(
            audit_type="gate_decision",
            gate_id=gate_id,
            risk_level=g.risk_level,
            action="gate_decision",
            decision=req.decision,
            reason=req.reason,
            project_id=g.project_id,
            run_id=g.run_id,
            stage=g.stage,
        )

        return g, audit

    def policy_check(self, req: PolicyCheckRequest) -> PolicyCheckResponse:
        """Mock policy check — always returns allowed=True in R4."""
        return PolicyCheckResponse(
            check_id=f"pc-{uuid.uuid4().hex[:6]}",
            allowed=True,
            reason="R4 mock: no real policy evaluation",
        )

    def risk_assess(self, req: RiskAssessmentRequest) -> RiskAssessmentResponse:
        """Mock risk assessment — always returns L0 in R4."""
        return RiskAssessmentResponse(
            assessment_id=f"ra-{uuid.uuid4().hex[:6]}",
            risk_level="L0",
            summary="R4 mock: no real risk assessment",
        )
