"""Toggle API — 启用 / 禁用 Skill / Agent / Resource。"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import SuccessEnvelope
from app.services.agent_service import AgentService
from app.services.registry_service import RegistryService
from app.services.skill_service import SkillService

logger = logging.getLogger("rebuild.routes_toggle")

toggle_router = APIRouter(prefix="/toggle", tags=["toggle"])


@toggle_router.patch("/skill/{skill_id}")
def toggle_skill(skill_id: str, db: Session = Depends(get_db)):
    svc = SkillService(db)
    skill = svc.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skill.enabled = not skill.enabled
    skill.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(skill)
    return SuccessEnvelope(
        data={"skill_id": skill_id, "enabled": skill.enabled},
        meta={"source_status": "real"},
    )


@toggle_router.patch("/agent/{agent_id}")
def toggle_agent(agent_id: str, db: Session = Depends(get_db)):
    svc = AgentService(db)
    agent = svc.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    from app.models.agent_definition import AgentCategory
    if agent.category == AgentCategory.system:
        raise HTTPException(status_code=403, detail="系统 Agent 不可禁用")
    agent.enabled = not agent.enabled
    agent.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(agent)
    return SuccessEnvelope(
        data={"agent_id": agent_id, "enabled": agent.enabled},
        meta={"source_status": "real"},
    )


@toggle_router.patch("/resource/{resource_id}")
def toggle_resource(resource_id: str, db: Session = Depends(get_db)):
    """启用/禁用一个资源。

    B-R21-TOGGLE-DISABLE-GATE：这是真实前端"资源"页启用/禁用按钮实际调用的端点
    （`ResourcesPage.tsx` `handleToggleResource` → `resourceService.toggleResource()` →
    `PATCH /api/toggle/resource/{id}`），此前直接 `entry.enabled = not entry.enabled`，
    完全绕开了 `PATCH /resources/{id}/disable`（B-R21-HOOK-DISABLE-GATE）与
    `PUT /resources/{id}`（B-R21-UPDATE-STATUS-GATE）刚建好的 Gate。

    只锁"安全关键资源 + enabled=True → False（关闭防护）"这一个具体方向；
    False → True（恢复防护）不需要 Gate。复用 `routes_registry` 里已有的
    `_security_critical_impl`/`_resolve_disable_gate`/`_create_disable_gate`
    （与 `disable_resource` 完全同一套 Gate——两个入口保护的是同一个动作，
    批准其中一个即视为批准了这次禁用），不重造一套判断/Gate 逻辑。
    """
    svc = RegistryService(db)
    entry = svc.get(resource_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Resource not found")

    if entry.enabled:
        from app.api.routes_registry import (
            _security_critical_impl, _resolve_disable_gate, _create_disable_gate,
        )
        if _security_critical_impl(entry):
            gate_state, gate_id = _resolve_disable_gate(resource_id)
            if gate_state == "approved":
                entry.enabled = False
                entry.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(entry)
                try:
                    from app.dependencies import get_services
                    gsvc = get_services()
                    gsvc.gate_service.mark_consumed(gate_id)
                    gsvc.audit_writer.write(
                        audit_type="gate_decision", gate_id=gate_id, risk_level="L4",
                        action=f"disable_resource:{resource_id}", decision="executed",
                        reason=(f"安全关键资源「{entry.name}」经 toggle 端点的禁用审批已通过，"
                                f"禁用已真正生效"),
                        project_id="platform",
                    )
                except Exception:
                    # 发声：消费/审计失败不回滚已生效的禁用，但必须可见（审计链完整性）。
                    logger.warning("toggle_resource: 审批通过后消费/审计失败 resource=%s",
                                   resource_id, exc_info=True)
                return SuccessEnvelope(
                    data={"resource_id": resource_id, "enabled": entry.enabled},
                    meta={"source_status": "real"},
                )
            if gate_state == "pending" and gate_id:
                return SuccessEnvelope(data={
                    "status": "awaiting_approval",
                    "resource_id": resource_id,
                    "gate_id": gate_id,
                    "gate_type": "action_approval",
                    "enabled": entry.enabled,  # 仍是 True——尚未真正禁用
                    "message": (f"安全关键资源「{entry.name}」的禁用已有等待中的 "
                                f"action_approval Gate（{gate_id}），审批通过后再次调用本接口"
                                f"方可真正禁用。"),
                }, meta={"source_status": "real"})
            gate_result = _create_disable_gate(resource_id, entry)
            return SuccessEnvelope(
                data={**gate_result, "enabled": entry.enabled},  # 仍是 True——尚未真正禁用
                meta={"source_status": "real"},
            )

    entry.enabled = not entry.enabled
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return SuccessEnvelope(
        data={"resource_id": resource_id, "enabled": entry.enabled},
        meta={"source_status": "real"},
    )
