"""Model Gateway API routes — R5 full implementation.

Endpoints:
  GET  /api/model/status       — aggregate gateway status
  GET  /api/model/providers    — provider list with credential_status
  GET  /api/model/profiles     — model profile list
  GET  /api/model/strategies   — model strategy list
  POST /api/model/self-test    — connectivity self-test
  POST /api/model/call         — model call via ModelGateway
  GET  /api/model/calls        — in-memory call log (volatile)
  POST /api/assistant/chat     — platform assistant chat
"""

from fastapi import APIRouter, Query

from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta
from app.schemas.model import (
    ProviderListData, ProviderResponse,
    ModelProfileListData, ModelProfileResponse,
    StrategyListData, StrategyResponse,
    ModelStatusResponse,
    SelfTestRequest, SelfTestResponse,
    ModelCallRequest, ModelCallResponse, UsageSummaryResponse,
    CallLogListData, CallLogEntry,
    AssistantChatRequest, AssistantChatResponse,
    ModelBindingResponse,
    CreateProviderRequest, SetCredentialRequest,
    UpdateProviderRequest,
    UsageResponse, UsageByProvider, UsageByModel,
    UpdateStrategyRequest,
)

model_router = APIRouter(prefix="/model", tags=["models"])
assistant_router = APIRouter(prefix="/assistant", tags=["assistant"])

# R13-8-FIX: Fusion 作为一等模型接入——固定虚拟服务商 id（无自有 Key）。
FUSION_PROVIDER_ID = "rebuild-fusion"


def _gateway():
    return get_services().model_gateway


def _fusion_availability(fp, gw) -> tuple[str, str]:
    """按底层参与模型可用性诚实计算一个 Fusion 虚拟模型的 (status, capability_marker)。

    - 未启用 → not_connected / disabled
    - 启用且 ≥1 参与模型已配置 → configured / real_available（可被选用）
    - 启用但无参与模型可达 → not_connected / not_connected（诚实降级，不伪装可用）
    rebuild-fusion 无自有 Key，credential 永远不参与判定、不回显。
    """
    if not fp.enabled:
        return "not_connected", "disabled"
    reg = gw.registry
    refs = [p.get("profile_ref") for p in (fp.panel_participants or []) if isinstance(p, dict)]
    reachable = 0
    for ref in refs:
        prof = reg.get_profile(ref) if ref else None
        if prof and prof.status == "configured":
            reachable += 1
    if reachable >= 1:
        return "configured", "real_available"
    return "not_connected", "not_connected"


def _fusion_profiles_raw() -> list:
    """读取全部 FusionProfile（真实 DB 行）。失败返回空（诚实，不阻断普通模型列表）。"""
    try:
        from app.core.database import get_session
        from app.services.fusion_profile_service import FusionProfileService
        db = get_session()
        try:
            rows, _ = FusionProfileService(db).list_all(limit=200, offset=0)
            return list(rows)
        finally:
            db.close()
    except Exception:
        return []


def _fusion_provider_response(gw) -> ProviderResponse:
    """合成 rebuild-fusion 虚拟服务商条目（聚合能力，无 Key，source_status=real）。"""
    rows = _fusion_profiles_raw()
    markers = [_fusion_availability(fp, gw)[1] for fp in rows]
    if any(m == "real_available" for m in markers):
        cap, status = "real_available", "available"
    elif rows:
        cap, status = "configured_not_verified", "not_connected"
    else:
        cap, status = "not_checked", "not_connected"
    return ProviderResponse(
        provider_id=FUSION_PROVIDER_ID,
        provider_name="Fusion 聚合",
        provider_type="fusion",
        api_format="openai",
        endpoint_openai="", endpoint_anthropic="",
        credential_status="configured",  # 无自有 Key，不要求填 Key、不回显
        key_source="none",
        status=status,
        model_count=len(rows),
        origin="seed",
        note="多模型聚合虚拟服务商（Panel→Judge→Synthesizer）。无自有密钥，凭据继承底层参与模型。配置入口：/fusion。",
        homepage="",
        last_checked_at="",
        capability_marker=cap,
        source_status="real",
        capability_status=status,
    )


def _fusion_profile_responses(gw) -> list[ModelProfileResponse]:
    """把每个 FusionProfile 呈现为一个可选"模型"（profile_id=virtual_profile_ref）。"""
    out: list[ModelProfileResponse] = []
    for fp in _fusion_profiles_raw():
        status, cap = _fusion_availability(fp, gw)
        n = len(fp.panel_participants or [])
        out.append(ModelProfileResponse(
            profile_id=fp.virtual_profile_ref,
            provider_id=FUSION_PROVIDER_ID,
            model_name=fp.name,
            display_name=fp.name,
            capability_tags=["fusion", "aggregation"],
            cost_tier="high",
            supports_streaming=True,
            supports_tool_calling=False,
            is_fusion_capable=False,  # Fusion 自身不能作为聚合参与模型（防递归）
            context_window_note=f"聚合 {n} 个参与模型",
            recommended_use="多模型聚合审议（Panel→Judge→Synthesizer）",
            not_recommended_use="",
            status=status,
            source_status="real",
            capability_status=cap,
        ))
    return out


def _provider_resp(p: dict) -> ProviderResponse:
    """从 gateway 的 provider dict 构建响应（含真实能力标记，不含 Key）。"""
    return ProviderResponse(
        provider_id=p["provider_id"],
        provider_name=p["provider_name"],
        provider_type=p["provider_type"],
        api_format=p["api_format"],
        endpoint_openai=p.get("endpoint_openai", ""),
        endpoint_anthropic=p.get("endpoint_anthropic", ""),
        credential_status=p["credential_status"],
        key_source=p.get("key_source", ""),
        status=p["status"],
        model_count=p.get("model_count", 0),
        origin=p.get("origin", "seed"),
        note=p.get("note", ""),
        homepage=p.get("homepage", ""),
        last_checked_at=p.get("last_checked_at", ""),
        capability_marker=p.get("capability_marker", "not_checked"),
        source_status=p["credential_status"],
        capability_status="available" if p["credential_status"] == "configured" else "not_connected",
    )


# ═══════════════════════════════════════════════════════════════════════
# Model Status
# ═══════════════════════════════════════════════════════════════════════

@model_router.get("/status")
async def model_status():
    gw = _gateway()
    status = gw.get_status()
    trace = get_services().trace_writer
    trace.write("state_change", action="model_status", summary=f"overall={status.overall_status}")
    return SuccessEnvelope(
        data=status.__dict__,
        meta=Meta(source_status=status.overall_status, capability_status="available"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Providers
# ═══════════════════════════════════════════════════════════════════════

@model_router.get("/providers")
async def list_providers():
    gw = _gateway()
    providers = gw.list_providers()
    resp = [_provider_resp(p) for p in providers]
    # R13-8-FIX: 并入 rebuild-fusion 虚拟服务商（聚合能力，无 Key）——像普通服务商一样被看到。
    resp.append(_fusion_provider_response(gw))
    get_services().trace_writer.write("state_change", action="list_providers",
                                       summary=f"{len(resp)} providers")
    return SuccessEnvelope(
        data=ProviderListData(providers=resp).model_dump(),
        meta=Meta(source_status="configured" if resp else "not_configured",
                   capability_status="available"),
    )


@model_router.post("/providers")
async def create_provider(req: CreateProviderRequest):
    """导入用户自定义供应商。

    非敏感配置持久化到 user_providers.yaml；api_key（如提供）仅注入进程内存
    （volatile，永不落盘，AGENTS.md §12.1）。
    """
    gw = _gateway()
    pid = (req.provider_id or req.provider_name or "").strip().lower().replace(" ", "-")
    if not pid:
        return SuccessEnvelope(
            data=None,
            meta=Meta(source_status="invalid", capability_status="available",
                       not_connected_reason="provider_name 不能为空"),
        )
    config = {
        "provider_id": pid,
        "provider_name": req.provider_name,
        "provider_type": req.provider_type,
        "api_format": req.api_format,
        "endpoint_openai": req.endpoint_openai,
        "endpoint_anthropic": req.endpoint_anthropic,
        "env_key_var": req.env_key_var,
        "note": req.note,
        "homepage": req.homepage,
        "models": [m.model_dump() for m in req.models],
    }
    try:
        p = gw.add_provider(config, api_key=req.api_key)
    except ValueError as e:
        return SuccessEnvelope(
            data=None,
            meta=Meta(source_status="conflict", capability_status="available",
                       not_connected_reason=str(e)),
        )
    get_services().trace_writer.write("state_change", action="create_provider",
                                       summary=f"provider={pid} key={'in_memory' if req.api_key else 'none'}")
    return SuccessEnvelope(
        data=_provider_resp(p).model_dump(),
        meta=Meta(source_status=p["credential_status"], capability_status="available"),
    )


@model_router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str):
    """删除用户导入的供应商（内置不可删）。"""
    gw = _gateway()
    ok = gw.remove_provider(provider_id)
    get_services().trace_writer.write("state_change", action="delete_provider",
                                       summary=f"provider={provider_id} removed={ok}")
    return SuccessEnvelope(
        data={"removed": ok},
        meta=Meta(source_status="removed" if ok else "not_found",
                   capability_status="available",
                   not_connected_reason="" if ok else "供应商不存在或为内置（不可删）"),
    )


@model_router.post("/providers/{provider_id}/credential")
async def set_provider_credential(provider_id: str, req: SetCredentialRequest):
    """为供应商（重新）设置 Key——仅进程内存，volatile，永不落盘。响应不回显 Key。"""
    gw = _gateway()
    p = gw.set_credential(provider_id, req.api_key)
    if not p:
        return SuccessEnvelope(
            data=None,
            meta=Meta(source_status="not_found", capability_status="available"),
        )
    get_services().trace_writer.write("state_change", action="set_credential",
                                       summary=f"provider={provider_id} credential updated")
    return SuccessEnvelope(
        data=_provider_resp(p).model_dump(),
        meta=Meta(source_status=p["credential_status"], capability_status="available"),
    )


    return SuccessEnvelope(
        data=_provider_resp(p).model_dump(),
        meta=Meta(source_status=p["credential_status"], capability_status="available"),
    )


@model_router.put("/providers/{provider_id}")
async def update_provider(provider_id: str, req: "UpdateProviderRequest"):
    """更新供应商非敏感配置（FB-005）：名称、端点、格式、模型列表等。"""
    from app.schemas.model import UpdateProviderRequest as _Unused  # noqa: F811
    gw = _gateway()
    kwargs = {}
    if req.provider_name is not None:
        kwargs["provider_name"] = req.provider_name
    if req.api_format is not None:
        kwargs["api_format"] = req.api_format
    if req.endpoint_openai is not None:
        kwargs["endpoint_openai"] = req.endpoint_openai
    if req.endpoint_anthropic is not None:
        kwargs["endpoint_anthropic"] = req.endpoint_anthropic
    if req.env_key_var is not None:
        kwargs["env_key_var"] = req.env_key_var
    if req.note is not None:
        kwargs["note"] = req.note
    if req.homepage is not None:
        kwargs["homepage"] = req.homepage
    if req.models is not None:
        kwargs["models"] = [m.model_dump() for m in req.models]
    p = gw.update_provider(provider_id, **kwargs)
    if not p:
        return SuccessEnvelope(data=None, meta=Meta(source_status="not_found", capability_status="available"))
    get_services().trace_writer.write("state_change", action="update_provider",
                                      summary=f"provider={provider_id} updated")
    return SuccessEnvelope(
        data=_provider_resp(p).model_dump(),
        meta=Meta(source_status=p["credential_status"], capability_status="available"),
    )


@model_router.get("/providers/{provider_id}")
async def get_provider(provider_id: str):
    gw = _gateway()
    p = gw.get_provider(provider_id)
    if not p:
        return SuccessEnvelope(
            data=None,
            meta=Meta(source_status="not_found", capability_status="available"),
        )
    return SuccessEnvelope(
        data=_provider_resp(p).model_dump(),
        meta=Meta(source_status=p["credential_status"], capability_status="available"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Model Profiles
# ═══════════════════════════════════════════════════════════════════════

@model_router.get("/profiles")
async def list_profiles(provider_id: str = Query(default="", description="Filter by provider")):
    gw = _gateway()
    profiles = gw.list_profiles(provider_id if provider_id else None)
    resp = [
        ModelProfileResponse(
            profile_id=p["profile_id"],
            provider_id=p["provider_id"],
            model_name=p["model_name"],
            display_name=p["display_name"],
            capability_tags=p["capability_tags"],
            cost_tier=p["cost_tier"],
            supports_streaming=p["supports_streaming"],
            supports_tool_calling=p["supports_tool_calling"],
            is_fusion_capable=p["is_fusion_capable"],
            context_window_note=p["context_window_note"],
            recommended_use=p["recommended_use"],
            not_recommended_use=p["not_recommended_use"],
            status=p["status"],
            source_status=p["status"],
            capability_status="available" if p["status"] == "configured" else "not_connected",
        )
        for p in profiles
    ]
    # R13-8-FIX: 并入 Fusion 虚拟模型（每个 FusionProfile = 一个可选模型）——像普通模型一样被选用。
    # 仅当无 provider 过滤，或明确过滤 rebuild-fusion 时并入。
    if not provider_id or provider_id == FUSION_PROVIDER_ID:
        resp.extend(_fusion_profile_responses(gw))
    return SuccessEnvelope(
        data=ModelProfileListData(profiles=resp).model_dump(),
        meta=Meta(source_status="configured" if resp else "not_configured",
                   capability_status="available"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Model Strategies
# ═══════════════════════════════════════════════════════════════════════

@model_router.get("/strategies")
async def list_strategies():
    gw = _gateway()
    strategies = gw.list_strategies()
    resp = [
        StrategyResponse(
            strategy_id=s["strategy_id"],
            scope=s["scope"],
            default_profile_ref=s["default_profile_ref"],
            fallback_profile_refs=s["fallback_profile_refs"],
            fallback_policy=s["fallback_policy"],
            retry_policy=s["retry_policy"],
            cost_budget_policy=s["cost_budget_policy"],
            fusion_allowed=s["fusion_allowed"],
            streaming_allowed=s["streaming_allowed"],
            tool_calling_allowed=s["tool_calling_allowed"],
            trace_policy=s["trace_policy"],
            audit_policy=s["audit_policy"],
        )
        for s in strategies
    ]
    return SuccessEnvelope(
        data=StrategyListData(strategies=resp).model_dump(),
        meta=Meta(source_status="available", capability_status="available"),
    )


@model_router.post("/strategies")
async def create_strategy(req: UpdateStrategyRequest):
    """创建新策略（FB-004 新增）。strategy_id 必填且不可与已有策略重复。"""
    gw = _gateway()
    if not req.strategy_id:
        return SuccessEnvelope(
            data=None,
            meta=Meta(source_status="invalid", capability_status="available",
                       not_connected_reason="strategy_id 不能为空"),
        )
    # 检查是否已存在
    existing = gw.list_strategies()
    if any(s["strategy_id"] == req.strategy_id for s in existing):
        return SuccessEnvelope(
            data=None,
            meta=Meta(source_status="conflict", capability_status="available",
                       not_connected_reason=f"策略 '{req.strategy_id}' 已存在"),
        )
    s = gw.create_strategy(
        strategy_id=req.strategy_id,
        default_profile_ref=req.default_profile_ref,
        fallback_profile_refs=req.fallback_profile_refs,
    )
    get_services().trace_writer.write("state_change", action="create_strategy",
                                      summary=f"strategy={req.strategy_id}")
    return SuccessEnvelope(
        data=StrategyResponse(
            strategy_id=s["strategy_id"], scope=s["scope"],
            default_profile_ref=s["default_profile_ref"], fallback_profile_refs=s["fallback_profile_refs"],
            fallback_policy=s["fallback_policy"], retry_policy=s["retry_policy"],
            cost_budget_policy=s["cost_budget_policy"], fusion_allowed=s["fusion_allowed"],
            streaming_allowed=s["streaming_allowed"], tool_calling_allowed=s["tool_calling_allowed"],
            trace_policy=s["trace_policy"], audit_policy=s["audit_policy"],
        ).model_dump(),
        meta=Meta(source_status="available", capability_status="available"),
    )


@model_router.delete("/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str):
    """删除策略（FB-007）。内置 system-default 不可删。"""
    if strategy_id == "system-default":
        return SuccessEnvelope(
            data={"removed": False},
            meta=Meta(source_status="forbidden", capability_status="available",
                       not_connected_reason="系统默认策略不可删除"),
        )
    gw = _gateway()
    ok = gw.delete_strategy(strategy_id)
    get_services().trace_writer.write("state_change", action="delete_strategy",
                                      summary=f"strategy={strategy_id} removed={ok}")
    return SuccessEnvelope(
        data={"removed": ok},
        meta=Meta(source_status="removed" if ok else "not_found",
                   capability_status="available",
                   not_connected_reason="" if ok else "策略不存在"),
    )


@model_router.put("/strategies/{strategy_id}")
async def update_strategy(strategy_id: str, req: UpdateStrategyRequest):
    """编辑策略：默认模型 + fallback 链（覆盖落盘 user_strategies.yaml，非敏感）。"""
    gw = _gateway()
    s = gw.update_strategy(strategy_id, req.default_profile_ref, req.fallback_profile_refs)
    if not s:
        return SuccessEnvelope(data=None, meta=Meta(source_status="not_found", capability_status="available"))
    get_services().trace_writer.write("state_change", action="update_strategy",
                                      summary=f"strategy={strategy_id} default={s.get('default_profile_ref','')}")
    return SuccessEnvelope(
        data=StrategyResponse(
            strategy_id=s["strategy_id"], scope=s["scope"],
            default_profile_ref=s["default_profile_ref"], fallback_profile_refs=s["fallback_profile_refs"],
            fallback_policy=s["fallback_policy"], retry_policy=s["retry_policy"],
            cost_budget_policy=s["cost_budget_policy"], fusion_allowed=s["fusion_allowed"],
            streaming_allowed=s["streaming_allowed"], tool_calling_allowed=s["tool_calling_allowed"],
            trace_policy=s["trace_policy"], audit_policy=s["audit_policy"],
        ).model_dump(),
        meta=Meta(source_status="available", capability_status="available"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Self-test
# ═══════════════════════════════════════════════════════════════════════

# Simple in-process rate limiter for self-test (C-2: minimal, not full rate-limit)
_last_self_test: dict[str, float] = {}
_SELF_TEST_COOLDOWN_SEC = 30.0


@model_router.post("/self-test")
async def self_test(req: SelfTestRequest):
    import time as _time

    # Rate limit check (C-2: process-internal cooldown only)
    now = _time.monotonic()
    cooldown_key = f"{req.provider_id}:{req.profile_id or 'default'}"
    if cooldown_key in _last_self_test:
        elapsed = now - _last_self_test[cooldown_key]
        if elapsed < _SELF_TEST_COOLDOWN_SEC:
            remaining = round(_SELF_TEST_COOLDOWN_SEC - elapsed, 0)
            return SuccessEnvelope(
                data=SelfTestResponse(
                    provider_id=req.provider_id,
                    status="rate_limited",
                    error_message=f"Self-test cooldown. Try again in {remaining}s.",
                ).model_dump(),
                meta=Meta(source_status="rate_limited", capability_status="available"),
            )

    _last_self_test[cooldown_key] = now

    gw = _gateway()
    result = await gw.self_test(req.provider_id, req.profile_id)

    # Write trace
    trace = get_services().trace_writer
    trace.write("model_call", action="self_test",
                summary=f"provider={req.provider_id} status={result['status']}")

    return SuccessEnvelope(
        data=SelfTestResponse(
            provider_id=result.get("provider_id", ""),
            profile_id=result.get("profile_id", ""),
            model=result.get("model", ""),
            status=result["status"],
            latency_ms=result.get("latency_ms", 0),
            credential_status=result.get("credential_status", "missing"),
            error_category=result.get("error_category", ""),
            error_message=result.get("error_message", ""),
            checked_at=result.get("checked_at", ""),
            call_id=result.get("call_id", ""),
        ).model_dump(),
        meta=Meta(source_status=result["status"], capability_status="available"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Model Call
# ═══════════════════════════════════════════════════════════════════════

@model_router.post("/call")
async def model_call(req: ModelCallRequest):
    gw = _gateway()
    result = await gw.call(
        messages=req.messages,
        user_override=req.user_override,
        strategy_id=req.strategy_id,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        stream=req.stream,
        source=req.source,
    )

    # Write trace
    trace = get_services().trace_writer
    trace.write("model_call", action="model_call",
                summary=f"status={result['status']} profile={result.get('profile_id','')}")

    if result["status"] == "completed":
        source_status = "configured"
    elif result["status"] == "blocked":
        source_status = result.get("error_category", "not_configured")
    else:
        source_status = result.get("error_category", "error")

    return SuccessEnvelope(
        data=ModelCallResponse(
            call_id=result.get("call_id", ""),
            status=result["status"],
            content=result.get("content", ""),
            model=result.get("model", ""),
            profile_id=result.get("profile_id", ""),
            provider_id=result.get("provider_id", ""),
            selection_reason=result.get("selection_reason", ""),
            latency_ms=result.get("latency_ms", 0),
            error_category=result.get("error_category", ""),
            error_message=result.get("error_message", ""),
            usage_summary=UsageSummaryResponse(**result.get("usage_summary", {})),
            retry_count=result.get("retry_count", 0),
            fallback_used=result.get("fallback_used", False),
        ).model_dump(),
        meta=Meta(source_status=source_status, capability_status="available"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Call Log
# ═══════════════════════════════════════════════════════════════════════

@model_router.get("/calls")
async def list_calls(limit: int = Query(default=10, le=100), offset: int = Query(default=0, ge=0)):
    gw = _gateway()
    calls, total = gw.list_calls(limit=limit, offset=offset)
    resp = [
        CallLogEntry(
            model_call_id=c["model_call_id"],
            provider_id=c.get("provider_id", ""),
            profile_id=c.get("profile_id", ""),
            strategy_id=c.get("strategy_id", ""),
            selected_model=c.get("selected_model", ""),
            selection_reason=c.get("selection_reason", ""),
            status=c["status"],
            latency_ms=c.get("latency_ms", 0),
            error_category=c.get("error_category", ""),
            retry_count=c.get("retry_count", 0),
            fallback_used=c.get("fallback_used", False),
            usage_summary=UsageSummaryResponse(**c.get("usage_summary", {})),
            source=c.get("source", "api"),
            created_at=c.get("created_at", ""),
            completed_at=c.get("completed_at", ""),
        )
        for c in calls
    ]
    return SuccessEnvelope(
        data=CallLogListData(calls=resp, total=total, limit=limit, offset=offset).model_dump(),
        meta=Meta(source_status="available", capability_status="available",
                   persistence="persisted"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Usage 聚合（进程内 call log，volatile）
# ═══════════════════════════════════════════════════════════════════════

@model_router.get("/usage")
async def get_usage():
    """真实用量聚合（token 真实，成本无计价数据→标记不可用，见 06 §9）。"""
    gw = _gateway()
    u = gw.get_usage()
    return SuccessEnvelope(
        data=UsageResponse(
            total_calls=u["total_calls"],
            completed_calls=u["completed_calls"],
            failed_calls=u["failed_calls"],
            prompt_tokens=u["prompt_tokens"],
            completion_tokens=u["completion_tokens"],
            total_tokens=u["total_tokens"],
            cache_hit_tokens=u.get("cache_hit_tokens", 0),
            cache_read_tokens=u.get("cache_read_tokens", 0),
            cache_hit_rate=u.get("cache_hit_rate", 0.0),
            cost_available=u["cost_available"],
            cost_unavailable_reason=u["cost_unavailable_reason"],
            by_provider=[UsageByProvider(**x) for x in u["by_provider"]],
            by_model=[UsageByModel(**x) for x in u["by_model"]],
            persisted=u.get("persisted", True),
        ).model_dump(),
        meta=Meta(source_status="available", capability_status="available",
                   persistence="persisted"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Platform Assistant Chat
# ═══════════════════════════════════════════════════════════════════════

@assistant_router.post("/chat")
async def assistant_chat(req: AssistantChatRequest):
    """Platform assistant chat — calls ModelGateway, not Provider directly.

    D-073: Assistant reuses ModelGateway; self-test only affects assistant session;
    does not change default ModelStrategy; does NOT perform platform operations on behalf of user.
    """
    gw = _gateway()
    result = await gw.call(
        messages=[{"role": "user", "content": req.message}],
        user_override=req.profile_id,
        strategy_id="system-default",
        max_tokens=2048,
        temperature=0.7,
        stream=False,
        source="platform_assistant",
    )

    trace = get_services().trace_writer
    trace.write("model_call", action="assistant_chat",
                summary=f"status={result['status']} profile={result.get('profile_id','')}")

    return SuccessEnvelope(
        data=AssistantChatResponse(
            reply=result.get("content", ""),
            model=result.get("model", ""),
            profile_id=result.get("profile_id", ""),
            provider_id=result.get("provider_id", ""),
            latency_ms=result.get("latency_ms", 0),
            status=result["status"],
            error_message=result.get("error_message", ""),
            source="ModelGateway",
        ).model_dump(),
        meta=Meta(source_status=result["status"], capability_status="available"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Legacy / Future endpoints
# ═══════════════════════════════════════════════════════════════════════

@model_router.get("/projects/{project_id}/binding")
async def get_model_binding(project_id: str):
    """Project-level model binding — future (R8 project settings)."""
    return SuccessEnvelope(
        data=ModelBindingResponse(),
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="Project model binding planned for R8"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Resource routes (placeholder — R6)
# ═══════════════════════════════════════════════════════════════════════

from app.schemas.resource import ResourceResponse, ResourceRegistryResponse

resource_router = APIRouter(prefix="/resources", tags=["resources"])


@resource_router.get("")
async def list_resources():
    get_services().trace_writer.write("state_change", action="list_resources", summary="Resources (placeholder)")
    return SuccessEnvelope(
        data=ResourceRegistryResponse(),
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="Resource registry planned for R6"),
    )


@resource_router.get("/registry")
async def get_registry():
    return SuccessEnvelope(
        data=ResourceRegistryResponse(),
        meta=Meta(source_status="not_connected", capability_status="future"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Integration routes (placeholder — R7)
# ═══════════════════════════════════════════════════════════════════════

from app.schemas.integration import IntegrationResponse, GitStatusResponse, SourceImportRequest, SourceImportResponse

integration_router = APIRouter(prefix="/projects/{project_id}/integrations", tags=["integrations"])


@integration_router.get("")
async def list_integrations(project_id: str):
    get_services().trace_writer.write("state_change", action="list_integrations",
                                      summary="Integrations (placeholder)", project_id=project_id)
    return SuccessEnvelope(
        data={"integrations": []},
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="Integration support planned for R7"),
    )


@integration_router.get("/git/status")
async def git_status(project_id: str):
    return SuccessEnvelope(
        data=GitStatusResponse(),
        meta=Meta(source_status="not_connected", capability_status="future"),
    )


@integration_router.post("/source/import")
async def source_import(project_id: str, req: SourceImportRequest):
    return SuccessEnvelope(
        data=SourceImportResponse(),
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="Source import planned for R7"),
    )
