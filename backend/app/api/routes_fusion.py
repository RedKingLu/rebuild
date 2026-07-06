"""Fusion configuration + execution API (R13-4 config + R13-5 engine). 9 endpoints.

Follows the established rebuild route pattern: APIRouter + SuccessEnvelope +
Depends(get_db). All endpoints are REAL (source_status=real, no mock/static data).
Configuration mutations (create / update / toggle) write an Audit entry via the
injected AuditWriter (R13-8-FIX F3; 09-聚合页 §4.6 #4 / R13-3 清单 #24).

R13-5: POST /profiles/{id}/trigger now runs FusionExecutionEngine.execute()
(Panel → Judge → Synthesizer, with Self-MoA / fallback / degraded), persisting a
fusion_run + participants and returning single content + fusion_metadata.

Endpoints:
  GET    /api/fusion/profiles
  POST   /api/fusion/profiles
  GET    /api/fusion/profiles/{id}
  PUT    /api/fusion/profiles/{id}/config
  POST   /api/fusion/profiles/{id}/toggle
  POST   /api/fusion/profiles/{id}/validate
  POST   /api/fusion/profiles/{id}/trigger     (R13-5: live execution)
  GET    /api/fusion/profiles/{id}/runs
  GET    /api/fusion/runs/{run_id}
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta
from app.schemas.fusion import (
    FusionProfileCreate,
    FusionProfileUpdate,
    FusionTriggerRequest,
)
from app.services.fusion_profile_service import (
    FusionProfileService,
    _profile_to_response,
    _run_to_response,
)

fusion_router = APIRouter(prefix="/fusion", tags=["fusion"])


def _svc(db: Session = Depends(get_db)) -> FusionProfileService:
    # R13-8-FIX (F3): 注入共享 AuditWriter，使配置变更真实落审计（非"by construction"空话）。
    return FusionProfileService(db, audit_writer=get_services().audit_writer)


# ── GET /api/fusion/profiles ─────────────────────────────────────────

@fusion_router.get("/profiles")
def list_profiles(limit: int = 50, offset: int = 0, svc: FusionProfileService = Depends(_svc)):
    rows, total = svc.list_all(limit=limit, offset=offset)
    return SuccessEnvelope(
        data={
            "profiles": [_profile_to_response(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        meta=Meta(source_status="real"),
    )


# ── POST /api/fusion/profiles ────────────────────────────────────────

@fusion_router.post("/profiles")
def create_profile(req: FusionProfileCreate, svc: FusionProfileService = Depends(_svc)):
    try:
        fp = svc.create(req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return SuccessEnvelope(
        data=_profile_to_response(fp),
        meta=Meta(source_status="real"),
    )


# ── GET /api/fusion/profiles/{id} ───────────────────────────────────

@fusion_router.get("/profiles/{profile_id}")
def get_profile(profile_id: str, svc: FusionProfileService = Depends(_svc)):
    fp = svc.get(profile_id)
    if fp is None:
        raise HTTPException(status_code=404, detail="Fusion profile not found")
    return SuccessEnvelope(
        data=_profile_to_response(fp),
        meta=Meta(source_status="real"),
    )


# ── PUT /api/fusion/profiles/{id}/config ─────────────────────────────

@fusion_router.put("/profiles/{profile_id}/config")
def update_profile(profile_id: str, req: FusionProfileUpdate, svc: FusionProfileService = Depends(_svc)):
    try:
        fp = svc.update(profile_id, req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if fp is None:
        raise HTTPException(status_code=404, detail="Fusion profile not found")
    return SuccessEnvelope(
        data=_profile_to_response(fp),
        meta=Meta(source_status="real"),
    )


# ── POST /api/fusion/profiles/{id}/toggle ────────────────────────────

@fusion_router.post("/profiles/{profile_id}/toggle")
def toggle_profile(profile_id: str, svc: FusionProfileService = Depends(_svc)):
    fp = svc.toggle(profile_id)
    if fp is None:
        raise HTTPException(status_code=404, detail="Fusion profile not found")
    return SuccessEnvelope(
        data=_profile_to_response(fp),
        meta=Meta(source_status="real"),
    )


# ── POST /api/fusion/profiles/{id}/validate ──────────────────────────

@fusion_router.post("/profiles/{profile_id}/validate")
def validate_existing(profile_id: str, svc: FusionProfileService = Depends(_svc)):
    fp = svc.get(profile_id)
    if fp is None:
        raise HTTPException(status_code=404, detail="Fusion profile not found")
    res = svc.validate(
        panel_participants=fp.panel_participants or [],
        judge=fp.judge,
        synthesizer=fp.synthesizer,
        style=fp.style,
        cost_limit=fp.cost_limit,
        timeout_seconds=fp.timeout_seconds,
        self_moa_enabled=fp.self_moa_enabled,
        exclude_profile_id=profile_id,
    )
    return SuccessEnvelope(
        data=res.model_dump(),
        meta=Meta(source_status="real"),
    )


# ── POST /api/fusion/profiles/{id}/trigger (R13-5 live execution) ─────

@fusion_router.post("/profiles/{profile_id}/trigger")
async def trigger_profile(
    profile_id: str,
    req: FusionTriggerRequest | None = None,
    db: Session = Depends(get_db),
):
    svc = FusionProfileService(db)
    fp = svc.get(profile_id)
    if fp is None:
        raise HTTPException(status_code=404, detail="Fusion profile not found")
    if not fp.enabled:
        raise HTTPException(status_code=409, detail="Fusion profile is disabled — enable before triggering")

    # Build user messages from request (default minimal prompt when none supplied).
    if req and req.message:
        messages = [{"role": "user", "content": req.message}]
    else:
        messages = [{"role": "user",
                     "content": "请对当前 Fusion 配置进行一轮多模型验证，确认各参与模型可正常调用。用中文简要输出融合结论。"}]

    # Resolve engine dependencies from the shared Services container.
    services = get_services()
    from app.services.fusion_execution_engine import FusionExecutionEngine

    engine = FusionExecutionEngine(
        db=db,
        gateway=services.model_gateway,
        trace_writer=services.trace_writer,
        audit_writer=services.audit_writer,
    )
    # override project/stage context if provided
    project_id = req.project_id if req else None
    stage = req.stage if req else None

    result = await engine.execute(fp, messages, project_id=project_id, stage=stage, source="manual")

    data = _profile_to_response(fp)
    data["trigger_status"] = result.status
    data["fusion_run_id"] = result.fusion_run_id
    data["strategy"] = result.strategy
    data["degraded"] = result.degraded
    data["degrade_reason"] = result.degrade_reason
    data["content"] = result.content
    data["fusion_metadata"] = result.fusion_metadata
    return SuccessEnvelope(
        data=data,
        meta=Meta(source_status="real",
                 capability_status="available" if result.status == "completed" else "degraded"),
        warnings=[result.error_message] if result.error_message else [],
    )


# ── GET /api/fusion/profiles/{id}/runs ──────────────────────────────

@fusion_router.get("/profiles/{profile_id}/runs")
def list_runs(profile_id: str, limit: int = 50, offset: int = 0, svc: FusionProfileService = Depends(_svc)):
    rows, total = svc.list_runs(profile_id, limit=limit, offset=offset)
    return SuccessEnvelope(
        data={
            "runs": [_run_to_response(r, None) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        meta=Meta(source_status="real"),
    )


# ── GET /api/fusion/runs/{run_id} ────────────────────────────────────

@fusion_router.get("/runs/{run_id}")
def get_run(run_id: str, svc: FusionProfileService = Depends(_svc)):
    res = svc.get_run(run_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Fusion run not found")
    run, participants = res
    return SuccessEnvelope(
        data=_run_to_response(run, participants),
        meta=Meta(source_status="real"),
    )
