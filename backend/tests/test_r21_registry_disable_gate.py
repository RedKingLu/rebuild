"""B-R21-HOOK-DISABLE-GATE 回归测试。

背景：`PATCH /resources/{resource_id}/disable` 此前对**任意**资源一视同仁——零风险
分级、零 Gate 审批、零审计记录，一次 API 调用即可让 D-032/D-099① 唯一的内容级检测
（`hook_engine._impl_pre_write_policy`，通过 `"pre-write Policy check"` 这条 hook 资源承载）
被无声关闭。本文件验证新增的风险升级路径：

  1. 禁用"安全关键"资源（`type_metadata.hook_impl` 命中
     `hook_engine._SECURITY_CRITICAL_IMPLS`）不再直接生效，而是创建 action_approval
     Gate 并返回 `awaiting_approval`，同时写一条 Audit（gate_created）。
  2. 用户通过既有 Gate 审批路由（`POST /projects/{project_id}/gates/{gate_id}/decision`）
     批准该 Gate 后，**再次调用**同一 disable 接口（re-dispatch 范式，与
     `tool_registry.execute_tool` 一致）才真正禁用生效，并写一条 Audit（executed）。
  3. 重复调用（未批准前）不得开出第二个 Gate——复用既有 pending Gate。
  4. 普通（非安全关键）资源的禁用行为完全不受影响，仍是原来的直接生效。
  5. Gate 被拒绝（reject）后资源仍保持启用，且再次调用会开出一个新 Gate（不会卡死）。

注：本文件不涉及任何真实凭据（AGENTS.md §8）。
"""

import uuid

import pytest  # noqa: F401

from app.core.database import get_session
from app.models.resource_entry import (
    ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus,
)

_SECURITY_HOOK_NAME = "pre-write Policy check"


def _insert_security_hook_resource() -> str:
    """插入一条真实形态的安全关键 hook 资源行（对齐 seed.py:213），返回 resource_id。"""
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
    """插入一条普通（非安全关键）hook 资源行，用作对照组。"""
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


def _get_enabled(client, resource_id: str) -> bool:
    resp = client.get(f"/api/resources/{resource_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["enabled"]


# ── 1. 安全关键资源禁用须走 Gate，不再直接生效 ─────────────────────────────────

def test_disable_security_critical_resource_returns_awaiting_approval_not_direct(client):
    rid = _insert_security_hook_resource()

    resp = client.patch(f"/api/resources/{rid}/disable")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("status") == "awaiting_approval", data
    assert data.get("gate_type") == "action_approval", data
    assert data.get("gate_id"), "未创建 action_approval Gate"

    # 关键断言：资源必须仍保持 enabled（尚未真正生效）
    assert _get_enabled(client, rid) is True, "未经批准，禁用竟已直接生效"


def test_disable_security_critical_resource_writes_audit_on_gate_creation(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource()

    client.patch(f"/api/resources/{rid}/disable")

    audits = get_services().audit_writer.list_all()
    matches = [a for a in audits
               if a.get("action") == f"disable_resource:{rid}"
               and a.get("decision") == "gate_created"]
    assert matches, "创建 Gate 时未写 Audit 记录"


# ── 2. 审批通过后 re-dispatch 才真正生效 ──────────────────────────────────────

def test_disable_takes_effect_only_after_gate_approved(client):
    rid = _insert_security_hook_resource()

    first = client.patch(f"/api/resources/{rid}/disable").json()["data"]
    gate_id = first["gate_id"]

    # 批准前再次调用：仍是 awaiting_approval，且必须复用同一个 gate_id（不开第二个门）
    second = client.patch(f"/api/resources/{rid}/disable").json()["data"]
    assert second["status"] == "awaiting_approval"
    assert second["gate_id"] == gate_id, "未批准时重复调用开出了新 Gate"
    assert _get_enabled(client, rid) is True

    # 批准
    decide = client.post(f"/api/projects/platform/gates/{gate_id}/decision",
                         json={"decision": "approve"})
    assert decide.status_code == 200, decide.text

    # 批准后再次调用同一接口 → 真正生效
    third = client.patch(f"/api/resources/{rid}/disable")
    assert third.status_code == 200, third.text
    third_data = third.json()["data"]
    assert third_data["enabled"] is False, third_data
    assert _get_enabled(client, rid) is False


def test_disable_writes_audit_on_actual_execution_after_approval(client):
    from app.dependencies import get_services
    rid = _insert_security_hook_resource()

    gate_id = client.patch(f"/api/resources/{rid}/disable").json()["data"]["gate_id"]
    client.post(f"/api/projects/platform/gates/{gate_id}/decision",
               json={"decision": "approve"})
    client.patch(f"/api/resources/{rid}/disable")

    audits = get_services().audit_writer.list_all()
    matches = [a for a in audits
               if a.get("action") == f"disable_resource:{rid}"
               and a.get("decision") == "executed"]
    assert matches, "审批通过并真正禁用后未写 Audit 记录"


def test_approved_gate_is_consumed_and_cannot_authorize_a_second_disable_cycle(client):
    """一次性消费：同一 Gate 批准后只驱动一次真正禁用；若资源被重新启用再禁用，
    须重新走审批（不得复用旧的已批准 Gate）。"""
    rid = _insert_security_hook_resource()
    gate_id = client.patch(f"/api/resources/{rid}/disable").json()["data"]["gate_id"]
    client.post(f"/api/projects/platform/gates/{gate_id}/decision",
               json={"decision": "approve"})
    client.patch(f"/api/resources/{rid}/disable")
    assert _get_enabled(client, rid) is False

    # 重新启用后再次禁用：不得因为存在一条"历史已批准 Gate"而直接生效
    client.patch(f"/api/resources/{rid}/enable")
    assert _get_enabled(client, rid) is True

    again = client.patch(f"/api/resources/{rid}/disable").json()["data"]
    assert again.get("status") == "awaiting_approval", (
        "旧 Gate 被复用，安全关键资源的第二次禁用未重新走审批")
    assert _get_enabled(client, rid) is True


# ── 3. 普通资源不受影响 ──────────────────────────────────────────────────────

def test_disable_non_critical_resource_still_takes_effect_directly(client):
    rid = _insert_non_critical_resource()

    resp = client.patch(f"/api/resources/{rid}/disable")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 普通资源直接生效：返回的是资源本体（含 enabled 字段），不是 Gate 状态
    assert data.get("enabled") is False, data
    assert "gate_id" not in data
    assert _get_enabled(client, rid) is False


def test_disable_non_critical_resource_creates_no_gate(client):
    from app.dependencies import get_services
    rid = _insert_non_critical_resource()
    client.patch(f"/api/resources/{rid}/disable")

    gates = get_services().gate_service.list_by_project("platform")
    assert not any(f"disable_resource:{rid}" in f"{g.reason or ''} {g.summary or ''}"
                   for g in gates), "普通资源的禁用竟创建了 Gate"


def test_disable_unknown_resource_still_returns_404(client):
    resp = client.patch("/api/resources/does-not-exist/disable")
    assert resp.status_code == 404


# ── 4. 拒绝后资源保持启用，且不会卡死（可再次发起审批） ─────────────────────────

def test_rejected_gate_leaves_resource_enabled_and_allows_retry(client):
    rid = _insert_security_hook_resource()
    gate_id = client.patch(f"/api/resources/{rid}/disable").json()["data"]["gate_id"]

    decide = client.post(f"/api/projects/platform/gates/{gate_id}/decision",
                         json={"decision": "reject"})
    assert decide.status_code == 200, decide.text
    assert _get_enabled(client, rid) is True, "Gate 被拒绝但资源已被禁用"

    retry = client.patch(f"/api/resources/{rid}/disable").json()["data"]
    assert retry.get("status") == "awaiting_approval"
    assert retry.get("gate_id") != gate_id, "拒绝后重试未开出新 Gate（可能卡死）"
