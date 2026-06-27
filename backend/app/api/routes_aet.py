"""AET (Artifact, Evidence, Trace, Audit) API routes."""

from fastapi import APIRouter, HTTPException

from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta

router = APIRouter(prefix="/projects/{project_id}", tags=["aet"])


def _svc():
    return get_services()


# --- Artifact ---
@router.get("/artifacts")
async def list_artifacts(project_id: str, stage: str | None = None):
    svc = _svc()
    artifacts = svc.aet_service.list_artifacts(stage=stage)
    svc.trace_writer.write("artifact_event", action="list_artifacts",
                           summary=f"Listed {len(artifacts)} artifacts", project_id=project_id)
    return SuccessEnvelope(data={"artifacts": [a.model_dump() for a in artifacts]}, meta=Meta())


@router.get("/artifacts/{artifact_id}")
async def get_artifact(project_id: str, artifact_id: str):
    svc = _svc()
    a = svc.aet_service.get_artifact(artifact_id)
    if a is None:
        raise HTTPException(404, f"Artifact {artifact_id} not found")
    return SuccessEnvelope(data=a, meta=Meta())


# --- Evidence ---
@router.get("/evidence")
async def list_evidence(project_id: str, stage: str | None = None):
    svc = _svc()
    evidences = svc.aet_service.list_evidence(stage=stage)
    svc.trace_writer.write("evidence_event", action="list_evidence",
                           summary=f"Listed {len(evidences)} evidence items", project_id=project_id)
    return SuccessEnvelope(data={"evidence": [e.model_dump() for e in evidences]}, meta=Meta())


@router.get("/evidence/{evidence_id}")
async def get_evidence(project_id: str, evidence_id: str):
    svc = _svc()
    e = svc.aet_service.get_evidence(evidence_id)
    if e is None:
        raise HTTPException(404, f"Evidence {evidence_id} not found")
    return SuccessEnvelope(data=e, meta=Meta())


@router.get("/evidence-gaps")
async def list_evidence_gaps(project_id: str):
    svc = _svc()
    gaps = svc.aet_service.list_evidence_gaps()
    return SuccessEnvelope(data={"gaps": [g.model_dump() for g in gaps]}, meta=Meta())


# --- Trace ---
@router.get("/trace")
async def list_traces(project_id: str, run_id: str | None = None,
                      stage: str | None = None, trace_type: str | None = None, limit: int = 50):
    svc = _svc()
    traces = svc.aet_service.list_traces(project_id=project_id, run_id=run_id,
                                         stage=stage, trace_type=trace_type, limit=limit)
    return SuccessEnvelope(data={"traces": traces, "persistence": "file+memory"}, meta=Meta())


@router.get("/trace/{trace_id}")
async def get_trace(project_id: str, trace_id: str):
    svc = _svc()
    t = svc.aet_service.get_trace(trace_id)
    if t is None:
        raise HTTPException(404, f"Trace {trace_id} not found")
    return SuccessEnvelope(data=t, meta=Meta())


# --- Audit ---
@router.get("/audit")
async def list_audits(project_id: str, gate_id: str | None = None,
                      risk_level: str | None = None, limit: int = 50):
    svc = _svc()
    audits = svc.aet_service.list_audits(project_id=project_id, gate_id=gate_id,
                                         risk_level=risk_level, limit=limit)
    return SuccessEnvelope(data={"audits": audits, "persistence": "file+memory"}, meta=Meta())


@router.get("/audit/{audit_id}")
async def get_audit(project_id: str, audit_id: str):
    svc = _svc()
    a = svc.aet_service.get_audit(audit_id)
    if a is None:
        raise HTTPException(404, f"Audit {audit_id} not found")
    return SuccessEnvelope(data=a, meta=Meta())
