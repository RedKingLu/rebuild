"""B-R21-UPDATE-STATUS-GATE 回归测试。

背景：`PATCH /resources/{id}/disable` 已经被加了 action_approval Gate
（B-R21-HOOK-DISABLE-GATE），但主窗口独立复核发现同一个不变量还有第二个入口——
`PUT /resources/{id}` 的 `ResourceUpdate.status` 字段可以把 status 改成任意非
`active` 值，而 `hook_engine._load_hooks()` 的加载条件是
`enabled==True AND status==active`：只要 status 不是 active，钩子照样不会被加载，
等于用另一个端点绕开了刚建好的 Gate。

本文件验证：`update_resource`（PUT）对安全关键资源、且请求体显式传了会让 status
偏离 active 的值时，同样必须走 action_approval Gate（复用
`_resolve_gate_for_action`/`_create_gate_for_action`，与 disable_resource 同一套
机制，只是 action 前缀是 `update_resource_status`）；而其余更新场景（不影响
status 的字段、status 仍是 active、非安全关键资源）完全不受影响，直接生效。
"""

import uuid

import pytest  # noqa: F401

from app.core.database import get_session
from app.models.resource_entry import (
    ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus,
)

_SECURITY_HOOK_NAME = "pre-write Policy check"


def _insert_security_hook_resource() -> str:
    db = get_session()
    try:
        rid = str(uuid.uuid4())
        db.add(ResourceEntry(
            resource_id=rid, name=_SECURITY_HOOK_NAME,
            resource_type=ResourceType.hook, source_type=SourceType.internal_current,
            source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
            status=ResourceStatus.active, enabled=True,
            description="任何文件写操作前的 Policy 校验",
            type_metadata={"hook_point": "PreToolUse", "hook_mode": "block",
                           "hook_impl": "pre_write_policy"}))
        db.commit()
        return rid
    finally:
        db.close()


def _insert_non_critical_resource() -> str:
    db = get_session()
    try:
        rid = str(uuid.uuid4())
        db.add(ResourceEntry(
            resource_id=rid, name="pre-commit quality check",
            resource_type=ResourceType.hook, source_type=SourceType.community,
            source_trust_level=TrustLevel.read_only_reference, risk_level=RiskLevel.L1,
            status=ResourceStatus.active, enabled=True,
            description="提交前 lint/secret/console.log 扫描",
            type_metadata={"hook_point": "PreToolUse", "hook_mode": "warn"}))
        db.commit()
        return rid
    finally:
        db.close()


def _get(client, resource_id: str) -> dict:
    resp = client.get(f"/api/resources/{resource_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. 安全关键资源：PUT status=非 active → 须走 Gate，不直接生效 ────────────────

def test_put_status_change_on_critical_resource_requires_gate(client):
    rid = _insert_security_hook_resource()

    resp = client.put(f"/api/resources/{rid}", json={"status": "deprecated"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") == "awaiting_approval", body
    assert body.get("gate_type") == "action_approval", body
    assert body.get("gate_id"), "未创建 action_approval Gate"

    # 关键断言：status 必须仍是 active（尚未真正生效）
    assert _get(client, rid)["status"] == "active", "未经批准，status 变更竟已直接生效"


def test_put_status_change_writes_audit_on_gate_creation(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource()

    client.put(f"/api/resources/{rid}", json={"status": "deprecated"})

    audits = get_services().audit_writer.list_all()
    matches = [a for a in audits
               if a.get("action") == f"update_resource_status:{rid}"
               and a.get("decision") == "gate_created"]
    assert matches, "创建 Gate 时未写 Audit 记录"


def test_put_status_change_takes_effect_only_after_gate_approved(client):
    rid = _insert_security_hook_resource()

    first = client.put(f"/api/resources/{rid}", json={"status": "deprecated"}).json()
    gate_id = first["gate_id"]

    # 未批准时重复调用：复用同一个 gate_id，不开第二个门
    second = client.put(f"/api/resources/{rid}", json={"status": "deprecated"}).json()
    assert second["status"] == "awaiting_approval"
    assert second["gate_id"] == gate_id, "未批准时重复调用开出了新 Gate"
    assert _get(client, rid)["status"] == "active"

    decide = client.post(f"/api/projects/platform/gates/{gate_id}/decision",
                         json={"decision": "approve"})
    assert decide.status_code == 200, decide.text

    third = client.put(f"/api/resources/{rid}", json={"status": "deprecated"})
    assert third.status_code == 200, third.text
    third_body = third.json()
    assert third_body["status"] == "deprecated", third_body
    assert _get(client, rid)["status"] == "deprecated"


def test_put_status_change_writes_audit_on_actual_execution_after_approval(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource()

    gate_id = client.put(f"/api/resources/{rid}", json={"status": "deprecated"}).json()["gate_id"]
    client.post(f"/api/projects/platform/gates/{gate_id}/decision", json={"decision": "approve"})
    client.put(f"/api/resources/{rid}", json={"status": "deprecated"})

    audits = get_services().audit_writer.list_all()
    matches = [a for a in audits
               if a.get("action") == f"update_resource_status:{rid}"
               and a.get("decision") == "executed"]
    assert matches, "审批通过并真正变更后未写 Audit 记录"


# ── 2. 安全关键资源：PUT 只改不影响 status 的字段 → 正常直接生效 ─────────────────

def test_put_non_status_field_on_critical_resource_takes_effect_directly(client):
    rid = _insert_security_hook_resource()

    resp = client.put(f"/api/resources/{rid}", json={"description": "更新后的描述"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("description") == "更新后的描述", body
    # 不是 Gate 状态返回（没有 gate_id/awaiting_approval）
    assert "gate_id" not in body
    assert body.get("status") != "awaiting_approval"
    # 数据库里真的直接生效了
    got = _get(client, rid)
    assert got["description"] == "更新后的描述"
    assert got["status"] == "active"


def test_put_non_status_field_on_critical_resource_creates_no_gate(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource()
    client.put(f"/api/resources/{rid}", json={"risk_level": "L3"})

    gates = get_services().gate_service.list_by_project("platform")
    assert not any(f"update_resource_status:{rid}" in f"{g.reason or ''} {g.summary or ''}"
                   for g in gates), "只改无关字段竟创建了 Gate"


# ── 3. 安全关键资源：PUT 显式传 status=active（值不变）→ 不需要 Gate，直接生效 ────

def test_put_status_active_explicit_on_critical_resource_takes_effect_directly(client):
    rid = _insert_security_hook_resource()

    resp = client.put(f"/api/resources/{rid}", json={"status": "active",
                                                      "description": "仍是 active"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") == "active"
    assert body.get("description") == "仍是 active"
    assert "gate_id" not in body
    assert _get(client, rid)["status"] == "active"


def test_put_status_active_explicit_on_critical_resource_creates_no_gate(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource()
    client.put(f"/api/resources/{rid}", json={"status": "active"})

    gates = get_services().gate_service.list_by_project("platform")
    assert not any(f"update_resource_status:{rid}" in f"{g.reason or ''} {g.summary or ''}"
                   for g in gates), "status=active（值不变）竟创建了 Gate"


# ── 4. 非安全关键资源：任何字段改动都不受影响 ─────────────────────────────────

def test_put_status_change_on_non_critical_resource_unaffected(client):
    rid = _insert_non_critical_resource()

    resp = client.put(f"/api/resources/{rid}", json={"status": "deprecated"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") == "deprecated", body
    assert "gate_id" not in body
    assert _get(client, rid)["status"] == "deprecated"


def test_put_status_change_on_non_critical_resource_creates_no_gate(client):
    from app.dependencies import get_services
    rid = _insert_non_critical_resource()
    client.put(f"/api/resources/{rid}", json={"status": "deprecated"})

    gates = get_services().gate_service.list_by_project("platform")
    assert not any(f"update_resource_status:{rid}" in f"{g.reason or ''} {g.summary or ''}"
                   for g in gates), "非安全关键资源的更新竟创建了 Gate"


def test_put_unknown_resource_still_returns_404(client):
    resp = client.put("/api/resources/does-not-exist", json={"status": "deprecated"})
    assert resp.status_code == 404
