"""AET service — Artifact, Evidence, Trace, Audit queries.

R4: Artifacts and Evidence are mock. Traces and Audits come from
the in-memory TraceWriter and AuditWriter respectively.
"""

from typing import Optional

from app.repositories.fixtures import (
    seed_artifacts, seed_evidences, seed_evidence_gaps,
)


class AETService:
    def __init__(self, services):
        self._svc = services
        self._artifacts: dict[str, object] = {}
        self._evidences: dict[str, object] = {}
        self._gaps: list[object] = []
        self._seed()

    def _seed(self):
        for a in seed_artifacts():
            self._artifacts[a.artifact_id] = a
        for e in seed_evidences():
            self._evidences[e.evidence_id] = e
        self._gaps = list(seed_evidence_gaps())

    # --- Artifact ---
    def list_artifacts(self, project_id: Optional[str] = None, stage: Optional[str] = None) -> list:
        results = list(self._artifacts.values())
        if stage:
            results = [a for a in results if getattr(a, 'stage', '') == stage]
        return results

    def get_artifact(self, artifact_id: str):
        return self._artifacts.get(artifact_id)

    # --- Evidence ---
    def list_evidence(self, project_id: Optional[str] = None, stage: Optional[str] = None) -> list:
        results = list(self._evidences.values())
        if stage:
            results = [e for e in results if getattr(e, 'stage', '') == stage]
        return results

    def get_evidence(self, evidence_id: str):
        return self._evidences.get(evidence_id)

    def list_evidence_gaps(self) -> list:
        return self._gaps

    # --- Trace (from trace_writer) ---
    def list_traces(self, project_id: Optional[str] = None, run_id: Optional[str] = None,
                    stage: Optional[str] = None, trace_type: Optional[str] = None, limit: int = 50) -> list:
        return self._svc.trace_writer.query(
            project_id=project_id, run_id=run_id, stage=stage,
            trace_type=trace_type, limit=limit,
        )

    def get_trace(self, trace_id: str):
        traces = self._svc.trace_writer.query(limit=10000)
        for t in traces:
            if t.get("trace_id") == trace_id:
                return t
        return None

    # --- Audit (from audit_writer) ---
    def list_audits(self, project_id: Optional[str] = None, gate_id: Optional[str] = None,
                    risk_level: Optional[str] = None, limit: int = 50) -> list:
        return self._svc.audit_writer.query(
            project_id=project_id, gate_id=gate_id,
            risk_level=risk_level, limit=limit,
        )

    def get_audit(self, audit_id: str):
        audits = self._svc.audit_writer.query(limit=10000)
        for a in audits:
            if a.get("audit_id") == audit_id:
                return a
        return None
