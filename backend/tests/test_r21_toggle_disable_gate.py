"""B-R21-TOGGLE-DISABLE-GATE 回归测试。

背景：主窗口独立复核确认，真实前端"资源"页的启用/禁用按钮
（`ResourcesPage.tsx:575` `handleToggleResource` → `resourceService.toggleResource()`）
实际调用的是 `PATCH /api/toggle/resource/{id}`（`routes_toggle.py::toggle_resource`），
它此前直接 `entry.enabled = not entry.enabled` 后 `db.commit()`，完全没有走
`disable_resource`（B-R21-HOOK-DISABLE-GATE）或 `update_resource`
（B-R21-UPDATE-STATUS-GATE）建好的任何 Gate——是三个绕过入口里跟真实使用路径最相关
的一个。

本文件验证：只锁"安全关键资源 + enabled=True → False"这一个具体方向（复用
`routes_registry._resolve_disable_gate`/`_create_disable_gate`，与 disable_resource
共用同一个 Gate）；False → True（恢复防护）与非安全关键资源的任意方向切换完全不受
影响，直接生效。
"""

import uuid

import pytest  # noqa: F401

from app.core.database import get_session
from app.models.resource_entry import (
    ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus,
)

_SECURITY_HOOK_NAME = "pre-write Policy check"


def _insert_security_hook_resource(enabled: bool = True) -> str:
    db = get_session()
    try:
        rid = str(uuid.uuid4())
        db.add(ResourceEntry(
            resource_id=rid, name=_SECURITY_HOOK_NAME,
            resource_type=ResourceType.hook, source_type=SourceType.internal_current,
            source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
            status=ResourceStatus.active, enabled=enabled,
            description="任何文件写操作前的 Policy 校验",
            type_metadata={"hook_point": "PreToolUse", "hook_mode": "block",
                           "hook_impl": "pre_write_policy"}))
        db.commit()
        return rid
    finally:
        db.close()


def _insert_non_critical_resource(enabled: bool = True) -> str:
    db = get_session()
    try:
        rid = str(uuid.uuid4())
        db.add(ResourceEntry(
            resource_id=rid, name="pre-commit quality check",
            resource_type=ResourceType.hook, source_type=SourceType.community,
            source_trust_level=TrustLevel.read_only_reference, risk_level=RiskLevel.L1,
            status=ResourceStatus.active, enabled=enabled,
            description="提交前 lint/secret/console.log 扫描",
            type_metadata={"hook_point": "PreToolUse", "hook_mode": "warn"}))
        db.commit()
        return rid
    finally:
        db.close()


def _get_enabled(client, resource_id: str) -> bool:
    resp = client.get(f"/api/resources/{resource_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["enabled"]


# ── 1. 安全关键资源，enabled=True → toggle：须走 Gate，不直接翻转 ────────────────

def test_toggle_security_critical_resource_from_enabled_requires_gate(client):
    rid = _insert_security_hook_resource(enabled=True)

    resp = client.patch(f"/api/toggle/resource/{rid}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("status") == "awaiting_approval", data
    assert data.get("gate_type") == "action_approval", data
    assert data.get("gate_id"), "未创建 action_approval Gate"
    # 前端假设 data.enabled 一定存在——即便走 Gate 分支也带上，且必须是真实值（仍 True）
    assert data.get("enabled") is True

    assert _get_enabled(client, rid) is True, "未经批准，toggle 竟已直接生效"


def test_toggle_reuses_the_same_gate_as_disable_resource_endpoint(client):
    """两个入口保护同一个动作：PATCH /disable 创建的 Gate，toggle 端点应识别为同一个
    pending Gate（不重复创建），反之亦然。"""
    rid = _insert_security_hook_resource(enabled=True)

    via_disable = client.patch(f"/api/resources/{rid}/disable").json()["data"]
    gate_id_from_disable = via_disable["gate_id"]

    via_toggle = client.patch(f"/api/toggle/resource/{rid}").json()["data"]
    assert via_toggle["gate_id"] == gate_id_from_disable, "toggle 端点开出了独立的第二个 Gate"


# ── 2. 安全关键资源，enabled=False → toggle：恢复防护，直接生效，不需要 Gate ───────

def test_toggle_security_critical_resource_from_disabled_takes_effect_directly(client):
    rid = _insert_security_hook_resource(enabled=False)

    resp = client.patch(f"/api/toggle/resource/{rid}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("enabled") is True, data
    assert "gate_id" not in data

    assert _get_enabled(client, rid) is True


def test_toggle_from_disabled_creates_no_gate(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource(enabled=False)
    client.patch(f"/api/toggle/resource/{rid}")

    gates = get_services().gate_service.list_by_project("platform")
    assert not any(f"disable_resource:{rid}" in f"{g.reason or ''} {g.summary or ''}"
                   for g in gates), "恢复防护方向竟创建了 Gate"


# ── 3. 非安全关键资源：任何方向都不受影响 ──────────────────────────────────────

def test_toggle_non_critical_resource_enable_to_disable_unaffected(client):
    rid = _insert_non_critical_resource(enabled=True)

    resp = client.patch(f"/api/toggle/resource/{rid}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("enabled") is False, data
    assert "gate_id" not in data
    assert _get_enabled(client, rid) is False


def test_toggle_non_critical_resource_disable_to_enable_unaffected(client):
    rid = _insert_non_critical_resource(enabled=False)

    resp = client.patch(f"/api/toggle/resource/{rid}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("enabled") is True, data
    assert _get_enabled(client, rid) is True


def test_toggle_unknown_resource_still_returns_404(client):
    resp = client.patch("/api/toggle/resource/does-not-exist")
    assert resp.status_code == 404


# ── 4. Gate 审批通过后再次调用 toggle，才真正生效为 disabled ─────────────────────

def test_toggle_takes_effect_only_after_gate_approved(client):
    rid = _insert_security_hook_resource(enabled=True)

    first = client.patch(f"/api/toggle/resource/{rid}").json()["data"]
    gate_id = first["gate_id"]

    # 未批准时重复调用：复用同一个 gate_id
    second = client.patch(f"/api/toggle/resource/{rid}").json()["data"]
    assert second["status"] == "awaiting_approval"
    assert second["gate_id"] == gate_id
    assert _get_enabled(client, rid) is True

    decide = client.post(f"/api/projects/platform/gates/{gate_id}/decision",
                         json={"decision": "approve"})
    assert decide.status_code == 200, decide.text

    third = client.patch(f"/api/toggle/resource/{rid}")
    assert third.status_code == 200, third.text
    third_data = third.json()["data"]
    assert third_data["enabled"] is False, third_data
    assert _get_enabled(client, rid) is False


def test_toggle_writes_audit_on_actual_execution_after_approval(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource(enabled=True)

    gate_id = client.patch(f"/api/toggle/resource/{rid}").json()["data"]["gate_id"]
    client.post(f"/api/projects/platform/gates/{gate_id}/decision", json={"decision": "approve"})
    client.patch(f"/api/toggle/resource/{rid}")

    audits = get_services().audit_writer.list_all()
    matches = [a for a in audits
               if a.get("action") == f"disable_resource:{rid}" and a.get("decision") == "executed"]
    assert matches, "审批通过并真正禁用后未写 Audit 记录"


# ── 5. toggle_skill / toggle_agent 不受影响（本次范围外） ──────────────────────

def test_toggle_skill_endpoint_unaffected_by_this_change(client):
    """回归锁：toggle_skill 本次不在范围内，行为必须原样保留。"""
    import uuid as _uuid
    from app.models.skill_definition import SkillDefinition, SkillSeries, SkillCategory, SkillStatus
    db = get_session()
    try:
        sid = str(_uuid.uuid4())
        db.add(SkillDefinition(skill_id=sid, name="Test Skill Toggle", series=SkillSeries.P,
                               category=SkillCategory.common, status=SkillStatus.active,
                               description="", enabled=True))
        db.commit()
    finally:
        db.close()

    resp = client.patch(f"/api/toggle/skill/{sid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["enabled"] is False
