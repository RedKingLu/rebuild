"""Resource Registry API routes — unified resource CRUD + query."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.registry import (
    ResourceCreate, ResourceUpdate, ResourceResponse,
    ResourceListData, RegistrySummary,
)
from app.services.registry_service import RegistryService
from app.schemas.common import SuccessEnvelope, Meta

logger = logging.getLogger("rebuild.routes_registry")

registry_router = APIRouter(prefix="/resources", tags=["resources"])

# 禁用"安全关键"资源（type_metadata.hook_impl 命中 hook_engine._SECURITY_CRITICAL_IMPLS，
# 目前唯一一条是承载 D-032/D-099① 写入期拦截的 "pre-write Policy check"）须走风险升级
# Gate（B-R21-HOOK-DISABLE-GATE），而非像普通资源那样零风险分级直接生效。资源登记本身不
# 属于任一项目，这里用固定 sentinel project_id 承载该类平台级 Gate（Gate.project_id 非空，
# 且现有 Gate 模型/审批路由都以 project_id 为路径段，没有"无项目 Gate"的既有形态）。
_REGISTRY_GATE_PROJECT = "platform"


def _security_critical_impl(entry) -> str | None:
    """解析 `entry` 实际绑定的 hook 实现体（走 `hook_engine._resolve_impl` 同一套解析——
    元数据 `hook_impl` 优先，否则按资源 `name` 确定性兜底绑定，见 B-R18-3），命中
    `_SECURITY_CRITICAL_IMPLS` 才返回该 impl_key，否则返回 None。

    用解析后的绑定而非直接读 `type_metadata.get("hook_impl")`，是为了不给"真实库该行缺
    hook_impl 键"（B-R18-3 已知的 seed 漂移）留一个绕过本 Gate 的空子——否则同一类漂移会
    让这里的安全关键判定失效，而 hook_engine 侧的运行期绑定却仍能命中。
    """
    from app.services.hook_engine import _resolve_impl, _SECURITY_CRITICAL_IMPLS
    meta = entry.type_metadata or {}
    impl_key, _impl = _resolve_impl(entry.name, meta)
    return impl_key if impl_key in _SECURITY_CRITICAL_IMPLS else None


def _resolve_gate_for_action(action: str, resource_id: str) -> tuple[str, str]:
    """解析 `{action}:{resource_id}` 这一动作对应的单一 action_approval Gate（通用版）。

    `disable_resource`（PATCH /disable）与"把 status 改到非 active"（PUT，
    B-R21-UPDATE-STATUS-GATE）保护的是同一个不变量——resource_id 是否还会被
    `hook_engine._load_hooks()` 加载——因此复用同一套 re-dispatch 范式（不重新发明），
    只用不同的 `action` 前缀让两者的 Gate 互不干扰。与
    `tool_registry._resolve_action_gate` 同一形状：已批准 → ("approved", gate_id)；
    等待中 → ("pending", gate_id)；否则 ("none", "")。绝不伪造 Gate。
    """
    try:
        from app.dependencies import get_services
        gates = get_services().gate_service.list_by_project(_REGISTRY_GATE_PROJECT)
    except Exception as e:
        logger.warning("_resolve_gate_for_action: gate 查询失败 action=%s resource=%s: %s",
                       action, resource_id, e)
        return ("none", "")

    marker = f"{action}:{resource_id}"

    def _matches(g) -> bool:
        if g.gate_type != "action_approval":
            return False
        blob = f"{g.reason or ''} {g.summary or ''}"
        return marker in blob

    approved = [g for g in gates if _matches(g) and g.gate_status == "approved"]
    if approved:
        return ("approved", approved[-1].gate_id)
    pending = [g for g in gates if _matches(g) and g.gate_status == "waiting_decision"]
    if pending:
        return ("pending", pending[-1].gate_id)
    return ("none", "")


def _create_gate_for_action(action: str, resource_id: str, entry, summary: str) -> dict:
    """为安全关键资源的某个动作创建 action_approval Gate（通用版，
    B-R21-HOOK-DISABLE-GATE / B-R21-UPDATE-STATUS-GATE 共用）。

    诚实降级：拿不到 GateService 时不伪造 gate_id，返回 risk_flagged 并说明原因
    （与 `tool_registry._create_risk_gate` 同一套红线）。
    """
    try:
        from app.dependencies import get_services
        svc = get_services()
    except Exception as e:
        return {
            "status": "risk_flagged",
            "resource_id": resource_id,
            "message": (f"资源「{entry.name}」是安全关键资源，此操作需人工审批；"
                        f"但当前运行上下文无可用 Gate 服务（{e}），未创建审批门。"),
        }
    try:
        gate = svc.gate_service.create(
            project_id=_REGISTRY_GATE_PROJECT, run_id="", stage="",
            gate_type="action_approval", risk_level="L4",
            reason=f"{action}:{resource_id} 安全关键资源「{entry.name}」需人工审批",
            summary=summary,
            options=["approve", "reject"],
        )
    except Exception as e:
        logger.warning("%s: action_approval gate 创建失败 resource=%s: %s",
                       action, resource_id, e)
        return {
            "status": "risk_flagged",
            "resource_id": resource_id,
            "message": f"安全关键资源此操作需人工审批；创建 action_approval Gate 失败（{e}）。",
        }
    try:
        svc.audit_writer.write(
            audit_type="gate_decision", gate_id=gate.gate_id, risk_level="L4",
            action=f"{action}:{resource_id}", decision="gate_created",
            reason=f"安全关键资源「{entry.name}」的此操作需人工审批，已创建 action_approval Gate",
            project_id=_REGISTRY_GATE_PROJECT,
        )
    except Exception:
        logger.warning("%s: gate 创建审计写入失败 resource=%s", action, resource_id,
                       exc_info=True)
    return {
        "status": "awaiting_approval",
        "resource_id": resource_id,
        "gate_id": gate.gate_id,
        "gate_type": "action_approval",
        "message": (f"资源「{entry.name}」是安全关键资源，此操作需人工审批，已创建 "
                    f"action_approval Gate（{gate.gate_id}）。审批通过（POST "
                    f"/projects/{_REGISTRY_GATE_PROJECT}/gates/{gate.gate_id}/decision，"
                    f"decision=approve）后再次调用本接口方可真正生效。"),
    }


def _resolve_disable_gate(resource_id: str) -> tuple[str, str]:
    return _resolve_gate_for_action("disable_resource", resource_id)


def _create_disable_gate(resource_id: str, entry) -> dict:
    summary = (f"拟禁用安全关键资源「{entry.name}」（resource_id={resource_id}）。"
               f"该资源承载安全红线检测（hook_impl 命中 _SECURITY_CRITICAL_IMPLS），禁用后"
               f"其对应的 Registry 驱动检测将不再运行（代码层不可关闭的检测不受影响）。"
               f"请审批。")
    return _create_gate_for_action("disable_resource", resource_id, entry, summary)


# B-R21-UPDATE-STATUS-GATE：`PATCH /disable` 不是唯一能让 `hook_engine._load_hooks()`
# 停止加载某条 hook 的入口——`PUT /resources/{id}` 的 `ResourceUpdate.status` 字段可以
# 把 status 改成任何非 active 值（`_load_hooks` 的加载条件是
# `enabled==True AND status==active`），从而绕开上面刚建好的禁用 Gate。保护的是同一个
# 不变量，故复用同一套 `_resolve_gate_for_action`/`_create_gate_for_action` 机制，只用
# 不同的 action 前缀（`update_resource_status`）区分 Gate。

def _resolve_update_status_gate(resource_id: str) -> tuple[str, str]:
    return _resolve_gate_for_action("update_resource_status", resource_id)


def _create_update_status_gate(resource_id: str, entry, new_status: str) -> dict:
    summary = (f"拟把安全关键资源「{entry.name}」（resource_id={resource_id}）的 status "
               f"改为「{new_status}」（非 active）。该资源承载安全红线检测（hook_impl 命中 "
               f"_SECURITY_CRITICAL_IMPLS），`hook_engine._load_hooks()` 只加载 "
               f"enabled=True 且 status=active 的行，status 改为非 active 后其对应的 "
               f"Registry 驱动检测将不再运行（代码层不可关闭的检测不受影响）。请审批。")
    return _create_gate_for_action("update_resource_status", resource_id, entry, summary)


def get_service(db: Session = Depends(get_db)) -> RegistryService:
    return RegistryService(db)


@registry_router.get("")
def list_resources(
    type: str | None = None,
    source: str | None = None,
    trust: str | None = None,
    risk: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, le=200),
    offset: int = Query(default=0, ge=0),
    svc: RegistryService = Depends(get_service),
):
    entries, total = svc.list_all(
        resource_type=type,
        source_type=source,
        trust_level=trust,
        risk_level=risk,
        status=status,
        limit=limit,
        offset=offset,
    )
    return SuccessEnvelope(data=ResourceListData(
        resources=[ResourceResponse(**svc.to_response(e)) for e in entries],
        total=total,
    ), meta=Meta())


@registry_router.get("/registry")
def registry_summary(svc: RegistryService = Depends(get_service)):
    return SuccessEnvelope(data=RegistrySummary(**svc.summary()), meta=Meta())


@registry_router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    entry = svc.get(resource_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return ResourceResponse(**svc.to_response(entry))


@registry_router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
def create_resource(data: ResourceCreate, svc: RegistryService = Depends(get_service)):
    entry = svc.create(data)
    return ResourceResponse(**svc.to_response(entry))


@registry_router.put("/{resource_id}")
def update_resource(resource_id: str, data: ResourceUpdate, svc: RegistryService = Depends(get_service)):
    """更新资源（普通字段直接生效）。

    B-R21-UPDATE-STATUS-GATE：`PATCH /disable` 不是唯一能让安全关键资源的 hook
    停止被 `hook_engine._load_hooks()` 加载的入口——本接口的 `status` 字段同样能做到
    （加载条件是 `enabled==True AND status==active`）。因此：安全关键资源
    （`_security_critical_impl(entry)` 命中）+ 请求体显式传了 `status` 且新值非
    `active` 时，本次更新须走 action_approval Gate（复用 disable_resource 的同一套
    机制），而不是直接生效；只锁"会导致 hook 失效"的这一具体变更——不影响 status 的
    更新（未传 status，或传了但仍是 active）正常直接生效，非安全关键资源完全不受影响。

    re-dispatch 范式（与 disable_resource / tool_registry.execute_tool 一致）：批准前
    返回 awaiting_approval；批准后再次调用本接口（携带同样的请求体）才真正生效。
    """
    entry = svc.get(resource_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    explicit_fields = data.model_dump(exclude_unset=True)
    new_status = explicit_fields.get("status")
    would_disarm_hook = "status" in explicit_fields and new_status != "active"

    if would_disarm_hook and _security_critical_impl(entry):
        gate_state, gate_id = _resolve_update_status_gate(resource_id)
        if gate_state == "approved":
            updated = svc.update(resource_id, data)
            if updated is None:
                raise HTTPException(status_code=404, detail="Resource not found")
            try:
                from app.dependencies import get_services
                gsvc = get_services()
                gsvc.gate_service.mark_consumed(gate_id)
                gsvc.audit_writer.write(
                    audit_type="gate_decision", gate_id=gate_id, risk_level="L4",
                    action=f"update_resource_status:{resource_id}", decision="executed",
                    reason=(f"安全关键资源「{entry.name}」的 status 变更（→ {new_status}）"
                            f"审批已通过，变更已真正生效"),
                    project_id=_REGISTRY_GATE_PROJECT,
                )
            except Exception:
                logger.warning("update_resource: 审批通过后消费/审计失败 resource=%s",
                               resource_id, exc_info=True)
            return ResourceResponse(**svc.to_response(updated))
        if gate_state == "pending" and gate_id:
            return {
                "status": "awaiting_approval",
                "resource_id": resource_id,
                "gate_id": gate_id,
                "gate_type": "action_approval",
                "message": (f"安全关键资源「{entry.name}」的 status 变更已有等待中的 "
                            f"action_approval Gate（{gate_id}），审批通过后再次以同样的请求体"
                            f"调用本接口方可真正生效。"),
            }
        return _create_update_status_gate(resource_id, entry, new_status)

    entry = svc.update(resource_id, data)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return ResourceResponse(**svc.to_response(entry))


@registry_router.delete("/{resource_id}", response_model=SuccessEnvelope)
def delete_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    """Soft-delete a resource (R15-4-C1): writes deleted_at, does not hard-delete."""
    if not svc.delete(resource_id):
        raise HTTPException(status_code=404, detail="Resource not found")
    return SuccessEnvelope(success=True, detail="Resource deleted")


# ── Enable / Disable (R15-4-C1) ─────────────────────────────────────────
# 平台内社区资源统一 启用/禁用/软删除；不再走 review 状态机。

@registry_router.patch("/{resource_id}/enable", response_model=SuccessEnvelope)
def enable_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    entry = svc.set_enabled(resource_id, True)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return SuccessEnvelope(data=svc.to_response(entry), detail="Resource enabled")


@registry_router.patch("/{resource_id}/disable", response_model=SuccessEnvelope)
def disable_resource(resource_id: str, svc: RegistryService = Depends(get_service)):
    """禁用一个资源（R15-4-C1）。

    B-R21-HOOK-DISABLE-GATE：安全关键资源（type_metadata.hook_impl 命中
    hook_engine._SECURITY_CRITICAL_IMPLS，如承载 D-032/D-099① 写入期拦截的
    "pre-write Policy check"）禁用前须经 action_approval Gate 人工审批 + 记 Audit——
    不再是零风险分级、零审计的普通资源管理调用。非安全关键资源行为不变（直接生效）。

    re-dispatch 范式（与 tool_registry.execute_tool 一致）：本接口是"幂等可重入"的——
    未批准时返回 awaiting_approval；批准后再次调用本接口，才真正执行禁用。
    """
    entry = svc.get(resource_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    critical_impl = _security_critical_impl(entry)
    if critical_impl:
        gate_state, gate_id = _resolve_disable_gate(resource_id)
        if gate_state == "approved":
            disabled = svc.set_enabled(resource_id, False)
            if disabled is None:
                raise HTTPException(status_code=404, detail="Resource not found")
            try:
                from app.dependencies import get_services
                gsvc = get_services()
                gsvc.gate_service.mark_consumed(gate_id)
                gsvc.audit_writer.write(
                    audit_type="gate_decision", gate_id=gate_id, risk_level="L4",
                    action=f"disable_resource:{resource_id}", decision="executed",
                    reason=f"安全关键资源「{entry.name}」禁用审批已通过，禁用已真正生效",
                    project_id=_REGISTRY_GATE_PROJECT,
                )
            except Exception:
                # 发声：消费/审计失败不回滚已生效的禁用，但必须可见（审计链完整性）。
                logger.warning("disable_resource: 审批通过后消费/审计失败 resource=%s",
                               resource_id, exc_info=True)
            return SuccessEnvelope(
                data=svc.to_response(disabled),
                detail="Resource disabled（安全关键资源，经 action_approval Gate 批准后生效）",
            )
        if gate_state == "pending" and gate_id:
            return SuccessEnvelope(data={
                "status": "awaiting_approval",
                "resource_id": resource_id,
                "gate_id": gate_id,
                "gate_type": "action_approval",
                "message": (f"安全关键资源「{entry.name}」的禁用已有等待中的 action_approval "
                            f"Gate（{gate_id}），审批通过后再次调用本接口方可真正禁用。"),
            })
        return SuccessEnvelope(data=_create_disable_gate(resource_id, entry))

    entry = svc.set_enabled(resource_id, False)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return SuccessEnvelope(data=svc.to_response(entry), detail="Resource disabled")


# ── Review state machine — DEPRECATED (R15-4-C1, D-061 修订执行注 2026-07-09) ──
# 社区资源合格性由发布侧保证；平台内不设资源审核状态机。approve/reject 退出主链路。
# 端点保留但返回 410 Gone，引导使用 启用/禁用/软删除 + L1-L5 动作风险。

@registry_router.post("/{resource_id}/review")
def review_resource_gone(resource_id: str):
    """DEPRECATED (410 Gone). 社区资源审核门已于 R15-4 废弃（D-061 修订）。

    请改用 PATCH /resources/{id}/enable、PATCH /resources/{id}/disable、
    DELETE /resources/{id}（软删除）。高危动作风险仍由 L1-L5 动作层处理。
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "resource review 状态机已废弃（R15-4，D-061 修订）：社区资源合格性由发布侧保证，"
            "平台内统一 启用/禁用/软删除 + L1-L5 动作风险。请改用 "
            "PATCH /resources/{id}/enable | /disable | DELETE /resources/{id}。"
        ),
    )


# T5.3 knowledge search endpoint moved to routes_knowledge.py (knowledge_router,
# prefix=/knowledge) so its URL is /api/knowledge/search, consistent with
# /api/knowledge/import-package. It was briefly on registry_router (prefix=/resources)
# which produced the misleading /api/resources/knowledge/search path.


def _read_body(entry) -> str | None:
    """Read a knowledge/case resource's Markdown body from its stored body_path."""
    meta = entry.type_metadata or {}
    body_path = meta.get("body_path")
    if body_path:
        from pathlib import Path
        p = Path(body_path)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    # fall back to source_path_or_ref directory's body.md
    ref = entry.source_path_or_ref
    if ref:
        from pathlib import Path
        p = Path(ref)
        candidate = p / "body.md" if p.is_dir() else p.parent / "body.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace")
    return None


# ── T5.3b: knowledge/case content (Markdown body) ────────────────────────

@registry_router.get("/{resource_id}/content")
def resource_content(resource_id: str, svc: RegistryService = Depends(get_service)):
    """Return the Markdown body of a knowledge/case resource (R15-4-C7)."""
    entry = svc.get(resource_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    body = _read_body(entry)
    return SuccessEnvelope(
        data={"resource_id": resource_id, "content": body, "content_type": "text/markdown"},
        meta={"source_status": "real", "has_body": body is not None},
    )
