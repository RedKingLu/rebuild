"""FusionProfile configuration service (R13-4).

DB-backed CRUD for fusion_profile / fusion_run / fusion_run_participant plus the
validator that enforces Fusion hard constraints (anti-recursion, no-tool synthesizer,
heterogeneity, budget/timeout). R13-5 wires the execution layer; this service is
the configuration layer only.

Validation design — intentionally does NOT require live LLM calls:
- participant profile EXISTENCE + credential_status come from the in-memory
  ModelGateway/ProviderRegistry (cheap, no network).
- heterogeneous-panel / self-moa-hint derives from the participant set structure.
- live reachability is surfaced as a WARNING only (R13-8 does live E2E).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fusion_profile import FusionProfile, FusionRun, FusionRunParticipant
from app.schemas.fusion import (
    ValidationResult,
    FusionProfileCreate,
    FusionProfileUpdate,
    FusionProfileResponse,
    FusionRunResponse,
    FusionRunParticipantResponse,
)


def _now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile_to_response(p: FusionProfile) -> dict:
    return {
        "fusion_profile_id": p.fusion_profile_id,
        "virtual_profile_ref": p.virtual_profile_ref,
        "name": p.name,
        "enabled": p.enabled,
        "panel_participants": p.panel_participants or [],
        "judge": p.judge or {},
        "synthesizer": p.synthesizer or {},
        "style": p.style,
        "enabled_stages": p.enabled_stages or [],
        "trigger": p.trigger,
        "cost_limit": p.cost_limit,
        "timeout_seconds": p.timeout_seconds,
        "audit_level": p.audit_level,
        "fallback_single_model": p.fallback_single_model,
        "excluded_providers": p.excluded_providers or [],
        "self_moa_enabled": p.self_moa_enabled,
        "max_fusion_depth": p.max_fusion_depth,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else "",
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "source_status": "real",
    }


def _run_to_response(r: FusionRun, participants: list[FusionRunParticipant] | None = None) -> dict:
    return {
        "fusion_run_id": r.fusion_run_id,
        "fusion_profile_id": r.fusion_profile_id,
        "project_id": r.project_id,
        "stage": r.stage,
        "source": r.source,
        "status": r.status,
        "strategy": r.strategy,
        "winner_ref": r.winner_ref,
        "confidence": r.confidence,
        "degraded": r.degraded,
        "degrade_reason": r.degrade_reason,
        "cost_sum": r.cost_sum,
        "latency_sum_ms": r.latency_sum_ms,
        "trace_refs": r.trace_refs or [],
        "audit_refs": r.audit_refs or [],
        "summary": r.summary,
        "participants": [
            {
                "id": p.id, "fusion_run_id": p.fusion_run_id, "role": p.role,
                "profile_ref": p.profile_ref, "provider_id": p.provider_id, "model": p.model,
                "perspective": p.perspective, "status": p.status, "latency_ms": p.latency_ms,
                "prompt_tokens": p.prompt_tokens, "completion_tokens": p.completion_tokens,
                "total_tokens": p.total_tokens, "cost": p.cost, "trace_ref": p.trace_ref,
                "model_call_id": p.model_call_id, "error_category": p.error_category,
            }
            for p in (participants or [])
        ],
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "source_status": "real",
    }


class FusionProfileService:
    """Configuration CRUD + validation for FusionProfile. R13-4."""

    def __init__(self, db: Session, audit_writer=None):
        self.db = db
        # R13-8-FIX (F3): 配置变更（create/update/toggle）写 Audit（09-聚合页 §4.6 #4 / R13-3 清单 #24）。
        # audit_writer 可选注入；未注入时静默跳过（不伪造"已写审计"）。
        self.audit_writer = audit_writer

    def _audit_config(self, action: str, fp: "FusionProfile", **extra) -> None:
        """记录一次 Fusion 配置变更审计（非高风险运行时；配置面白名单变更）。"""
        if not self.audit_writer:
            return
        try:
            self.audit_writer.write(
                "fusion_config_change",
                risk_level="L1",
                action=action,
                decision="applied",
                reason=f"fusion_profile={fp.virtual_profile_ref}",
                fusion_profile_id=fp.fusion_profile_id,
                enabled=fp.enabled,
                name=fp.name,
            )
        except Exception:
            pass

    # ── Provider lookups (cached per request) ──────────────────────────

    def _list_profiles(self) -> list[dict]:
        """Thin wrapper around ModelGateway.list_profiles()."""
        from app.dependencies import get_services
        try:
            return get_services().model_gateway.list_profiles() or []
        except Exception:
            return []

    def _get_profile(self, profile_ref: str) -> Optional[dict]:
        from app.dependencies import get_services
        try:
            return get_services().model_gateway.get_profile(profile_ref)
        except Exception:
            return None

    def _profile_virtual_refs(self) -> set[str]:
        """All live Fusion virtual refs — for anti-recursion validation."""
        rows = self.db.execute(select(FusionProfile.virtual_profile_ref)).scalars().all()
        return set(rows)

    # ── Validation (WP-4.2 — core) ───────────────────────────────────

    def validate(
        self,
        panel_participants: list[dict],
        judge: dict | None,
        synthesizer: dict | None,
        style: str = "balanced",
        cost_limit: float = 0.0,
        timeout_seconds: int = 120,
        self_moa_enabled: bool = True,
        exclude_profile_id: str | None = None,
    ) -> ValidationResult:
        """Static validation — no DB write, no live LLM call.

        Checks:
          - panel has >= 1 participant, every participant has profile_ref
          - HARD: no participant references an existing Fusion virtual model (anti-recursion)
          - HARD: no participant equals this profile's own virtual ref (self-recursion)
          - judge present with profile_ref
          - synthesizer present, NO tool config (方案 E)
          - heterogeneity: warn if <2 distinct providers OR all same provider (→ Self-MoA)
          - participant existence against ModelGateway (warn if missing)
          - budget / timeout sanity (warn)
        """
        errors: list[str] = []
        warnings: list[str] = []

        vrefs = self._profile_virtual_refs()
        # exclude self when re-validating on update
        if exclude_profile_id:
            row = self.db.get(FusionProfile, exclude_profile_id)
            if row and row.virtual_profile_ref in vrefs:
                vrefs = vrefs - {row.virtual_profile_ref}

        # 1. panel non-empty, every entry has profile_ref
        if not panel_participants:
            errors.append("panel_participants 不能为空：至少需要一个参与模型")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

        prof_map = {p.get("profile_id", p.get("profile_ref", "")): p for p in self._list_profiles()}
        providers: list[str] = []
        for i, pp in enumerate(panel_participants):
            ref = pp.get("profile_ref")
            if not ref:
                errors.append(f"panel_participants[{i}] 缺少 profile_ref")
                continue
            # HARD: anti-recursion — participant must not be a Fusion virtual model
            if ref in vrefs:
                errors.append(f"panel_participants[{i}] profile_ref='{ref}' 是一个 Fusion 虚拟模型，禁止 Fusion 调用 Fusion（防递归，max_fusion_depth=1）")
            # existence check (warn only)
            if ref not in prof_map:
                warnings.append(f"panel_participants[{i}] profile_ref='{ref}' 在当前 ModelGateway 中未找到（可能未配置 Key）")
            else:
                providers.append(ref.split("/")[0] if "/" in ref else ref)

        # 2. judge
        if not judge or not judge.get("profile_ref"):
            errors.append("judge.profile_ref 必填")
        elif judge.get("profile_ref") in vrefs:
            errors.append(f"judge.profile_ref='{judge['profile_ref']}' 是 Fusion 虚拟模型（防递归）")
        if judge and judge.get("profile_ref") and judge["profile_ref"] not in prof_map:
            warnings.append(f"judge.profile_ref='{judge['profile_ref']}' 在当前 ModelGateway 中未找到")

        # 3. synthesizer — NO tool config (方案 E hard constraint)
        if not synthesizer or not synthesizer.get("profile_ref"):
            errors.append("synthesizer.profile_ref 必填")
        else:
            forbidden_keys = {"tool", "tool_policy", "tools", "tool_calling", "supports_tool_calling"}
            violators = [k for k in synthesizer if k.lower() in forbidden_keys]
            if violators:
                errors.append(f"synthesizer 不得包含工具配置字段 {violators}（方案 E：Fusion 不执行工具）")
            if synthesizer.get("profile_ref") in vrefs:
                errors.append(f"synthesizer.profile_ref='{synthesizer['profile_ref']}' 是 Fusion 虚拟模型（防递归）")
            if synthesizer.get("profile_ref") and synthesizer["profile_ref"] not in prof_map:
                warnings.append(f"synthesizer.profile_ref='{synthesizer['profile_ref']}' 在当前 ModelGateway 中未找到")

        # 4. heterogeneity hint (warn)
        distinct = set(providers)
        if providers and len(distinct) < 2:
            if self_moa_enabled:
                warnings.append(f"所有参与模型均来自同一 provider ({distinct.pop() if distinct else '?'}) — 将进入 Self-MoA 标准降级（strategy=self_moa）")
            else:
                warnings.append("所有参与模型均来自同一 provider，且 Self-MoA 未启用，建议启用以避免单点")

        # 5. budget/timeout sanity
        if cost_limit < 0:
            errors.append("cost_limit 不能为负")
        if timeout_seconds < 5:
            warnings.append("timeout_seconds < 5s 过短，建议 ≥ 30s")

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    # ── CRUD ──────────────────────────────────────────────────────────

    def create(self, data: FusionProfileCreate) -> FusionProfile:
        panel = [p.model_dump() for p in data.panel_participants] if data.panel_participants else []
        judge = data.judge.model_dump() if data.judge else {}
        synth = data.synthesizer.model_dump() if data.synthesizer else {}
        g = data.global_config

        # validator gate
        res = self.validate(
            panel_participants=panel,
            judge=judge,
            synthesizer=synth,
            style=g.style,
            cost_limit=g.cost_limit,
            timeout_seconds=g.timeout_seconds,
            self_moa_enabled=g.self_moa_enabled,
        )
        if not res.valid:
            raise ValueError("；".join(res.errors))

        fp = FusionProfile(
            virtual_profile_ref=f"fusion/",  # placeholder — replaced below with deterministic id
            name=data.name,
            enabled=False,
            panel_participants=panel,
            judge=judge,
            synthesizer=synth,
            style=g.style,
            enabled_stages=g.enabled_stages,
            trigger=g.trigger,
            cost_limit=g.cost_limit,
            timeout_seconds=g.timeout_seconds,
            audit_level=g.audit_level,
            fallback_single_model=g.fallback_single_model,
            excluded_providers=g.excluded_providers,
            self_moa_enabled=g.self_moa_enabled,
            max_fusion_depth=1,
            created_by="api",
        )
        self.db.add(fp)
        self.db.flush()  # obtain id
        fp.virtual_profile_ref = f"fusion/{fp.fusion_profile_id}"
        self.db.commit()
        self.db.refresh(fp)
        self._audit_config("fusion_profile_create", fp)
        return fp

    def update(self, profile_id: str, data: FusionProfileUpdate) -> Optional[FusionProfile]:
        fp = self.db.get(FusionProfile, profile_id)
        if fp is None:
            return None

        panel = fp.panel_participants
        judge = fp.judge
        synthesizer = fp.synthesizer
        style = fp.style
        cost_limit = fp.cost_limit
        timeout_seconds = fp.timeout_seconds
        self_moa_enabled = fp.self_moa_enabled

        if data.name is not None:
            fp.name = data.name
        if data.panel_participants is not None:
            panel = [p.model_dump() for p in data.panel_participants]
            fp.panel_participants = panel
        if data.judge is not None:
            judge = data.judge.model_dump()
            fp.judge = judge
        if data.synthesizer is not None:
            synthesizer = data.synthesizer.model_dump()
            fp.synthesizer = synthesizer
        if data.global_config is not None:
            g = data.global_config
            fp.style = g.style
            fp.enabled_stages = g.enabled_stages
            fp.trigger = g.trigger
            fp.cost_limit = g.cost_limit
            fp.timeout_seconds = g.timeout_seconds
            fp.audit_level = g.audit_level
            fp.fallback_single_model = g.fallback_single_model
            fp.excluded_providers = g.excluded_providers
            fp.self_moa_enabled = g.self_moa_enabled
            style, cost_limit, timeout_seconds, self_moa_enabled = g.style, g.cost_limit, g.timeout_seconds, g.self_moa_enabled

        # re-validate against current state (exclude self to allow idempotent update)
        res = self.validate(
            panel_participants=panel,
            judge=judge,
            synthesizer=synthesizer,
            style=style,
            cost_limit=cost_limit,
            timeout_seconds=timeout_seconds,
            self_moa_enabled=self_moa_enabled,
            exclude_profile_id=profile_id,
        )
        if not res.valid:
            raise ValueError("；".join(res.errors))

        fp.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(fp)
        self._audit_config("fusion_profile_update", fp)
        return fp

    def get(self, profile_id: str) -> Optional[FusionProfile]:
        return self.db.get(FusionProfile, profile_id)

    def list_all(self, limit: int = 50, offset: int = 0) -> tuple[list[FusionProfile], int]:
        total = self.db.query(FusionProfile).count()
        rows = (
            self.db.query(FusionProfile)
            .order_by(FusionProfile.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total

    def toggle(self, profile_id: str) -> Optional[FusionProfile]:
        fp = self.db.get(FusionProfile, profile_id)
        if fp is None:
            return None
        fp.enabled = not fp.enabled
        fp.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(fp)
        self._audit_config("fusion_profile_toggle", fp)
        return fp

    def delete(self, profile_id: str) -> bool:
        fp = self.db.get(FusionProfile, profile_id)
        if fp is None:
            return False
        self.db.delete(fp)
        self.db.commit()
        return True

    # ── Runs queries (read-only in R13-4; R13-5 populates runs) ──────

    def list_runs(self, profile_id: str, limit: int = 50, offset: int = 0) -> tuple[list[FusionRun], int]:
        total = self.db.query(FusionRun).filter(FusionRun.fusion_profile_id == profile_id).count()
        rows = (
            self.db.query(FusionRun)
            .filter(FusionRun.fusion_profile_id == profile_id)
            .order_by(FusionRun.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total

    def get_run(self, run_id: str) -> Optional[tuple[FusionRun, list[FusionRunParticipant]]]:
        r = self.db.get(FusionRun, run_id)
        if r is None:
            return None
        parts = (
            self.db.query(FusionRunParticipant)
            .filter(FusionRunParticipant.fusion_run_id == run_id)
            .all()
        )
        return r, parts
