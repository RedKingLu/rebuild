"""R17.2 — action_approval Gate one-time consumption (R18→R17.2).

Before this fix, an approved action_approval Gate authorized a re-dispatch forever, so
one approval could be reused for unlimited high-risk writes. Now a successful gate-approved
re-dispatch of a high-risk write/execute tool marks the gate "consumed"; the next call
finds no approved/waiting gate and opens a fresh pending one (awaiting_approval).

Uses the autouse isolated_data fixture (temp DB + workspace) from conftest.
"""

import pytest  # noqa: F401  (pytest-asyncio auto mode collects async tests)

from app.dependencies import get_services
from app.core.database import get_session
from app.services import tool_registry
from app.services.workspace_service import init_workspace
from app.models.resource_entry import (
    ResourceEntry, ResourceType, ResourceStatus, RiskLevel,
)
from app.models.gate import Gate


_PID = "proj-gate-consume"
_RUN = "run-consume"
_TOOL = "apply_patch_with_confirm"


def _seed_high_risk_tool():
    """Insert an L4 output_code tool so execute_tool routes to _execute_apply_patch."""
    db = get_session()
    try:
        entry = ResourceEntry(
            resource_type=ResourceType.tool,
            name="Apply Patch With Confirm",
            description="apply a patch draft into output_code (L4, gated)",
            risk_level=RiskLevel.L4,
            status=ResourceStatus.active,
            enabled=True,
            type_metadata={"tool_name": _TOOL, "write_scope": "output_code"},
        )
        db.add(entry)
        db.commit()
    finally:
        db.close()


async def _make_full_content_draft(target_rel="output_code/consume_target.txt"):
    drafted = await tool_registry._execute_generate_patch(
        "generate_patch",
        {"target_path": target_rel, "diff": "consumed-tool-output\n"},
        _PID, None,
    )
    assert drafted["status"] == "patch_drafted"
    return drafted["patch_ref"], target_rel


def _create_approved_gate(args: dict | None = None) -> str:
    """Create a real action_approval gate matching the tool, then approve it.

    B-ACC-GATE-APPROVAL-NOT-BOUND（如实说明测试改动）：本 helper 原先建 Gate 时不绑定任何
    入参指纹——这正是该缺陷的形态本身（"批准"只按工具名生效，与被执行的具体入参无关）。
    `_resolve_action_gate` 现在要求 `action_fingerprint` 与本次调用入参的指纹一致才授权
    （指纹缺失 fail-closed，不匹配）；下面两个用到本 helper 的测试原本依赖"无指纹也能
    授权"这一不安全行为，现改为传入**将被执行的那一份 `args`** 并据此计算指纹，使"批准"
    真正绑定到被审阅的入参——这正是本批次要验证的目标，不是放宽守卫掩盖失败。
    """
    gs = get_services().gate_service
    fingerprint = (tool_registry.action_args_fingerprint(_TOOL, args)
                  if args is not None else None)
    gate = gs.create(
        project_id=_PID, run_id=_RUN, stage="p4",
        gate_type="action_approval", risk_level="L4",
        reason=f"高风险工具 {_TOOL} 执行前需人工审批",
        summary=f"Agent 拟执行高风险工具 {_TOOL}，请审批",
        options=["approve", "reject"],
        action_fingerprint=fingerprint,
    )
    gid = gate.gate_id
    # Approve it directly in the DB (deterministic, no promotion side-effects).
    db = get_session()
    try:
        g = db.get(Gate, gid)
        g.gate_status = "approved"
        db.commit()
    finally:
        db.close()
    return gid


async def test_gate_consumed_after_successful_highrisk_write():
    init_workspace(_PID)
    _seed_high_risk_tool()
    patch_ref, target_rel = await _make_full_content_draft()
    gid = _create_approved_gate({"patch_ref": patch_ref, "target_path": target_rel})

    gs = get_services().gate_service
    assert gs.get(gid).gate_status == "approved"

    # First call: approved gate → re-dispatch → real write succeeds.
    db = get_session()
    try:
        result = await tool_registry.execute_tool(
            _TOOL, {"patch_ref": patch_ref, "target_path": target_rel},
            _PID, stage="p4", db=db, run_id=_RUN,
        )
    finally:
        db.close()
    assert result["status"] == "patch_applied", result

    # The gate must now be consumed — one-time approval.
    assert gs.get(gid).gate_status == "consumed"

    # Second call with the SAME tool: no approved/waiting gate remains, so a fresh
    # pending gate is opened and execution is blocked (awaiting_approval).
    db = get_session()
    try:
        result2 = await tool_registry.execute_tool(
            _TOOL, {"patch_ref": patch_ref, "target_path": target_rel},
            _PID, stage="p4", db=db, run_id=_RUN,
        )
    finally:
        db.close()
    assert result2["status"] in ("awaiting_approval", "risk_flagged"), result2
    # It did NOT reuse the consumed gate.
    assert result2.get("gate_id") != gid


async def test_consumption_writes_audit():
    init_workspace(_PID)
    _seed_high_risk_tool()
    patch_ref, target_rel = await _make_full_content_draft()
    gid = _create_approved_gate({"patch_ref": patch_ref, "target_path": target_rel})

    db = get_session()
    try:
        await tool_registry.execute_tool(
            _TOOL, {"patch_ref": patch_ref, "target_path": target_rel},
            _PID, stage="p4", db=db, run_id=_RUN,
        )
    finally:
        db.close()

    audits = get_services().audit_writer.query(project_id=_PID, limit=100)
    consumed = [a for a in audits if a.get("action") == "gate_consumed" and a.get("gate_id") == gid]
    assert consumed, f"expected a gate_consumed audit for {gid}, got {audits}"
    assert consumed[0]["decision"] == "consumed"


async def test_confirmed_true_does_not_consume_gate():
    """confirmed=True is a direct-thread with no gate_id → nothing is consumed."""
    init_workspace(_PID)
    _seed_high_risk_tool()
    patch_ref, target_rel = await _make_full_content_draft()
    gid = _create_approved_gate({"patch_ref": patch_ref, "target_path": target_rel})

    gs = get_services().gate_service
    db = get_session()
    try:
        result = await tool_registry.execute_tool(
            _TOOL, {"patch_ref": patch_ref, "target_path": target_rel},
            _PID, stage="p4", db=db, run_id=_RUN, confirmed=True,
        )
    finally:
        db.close()
    assert result["status"] == "patch_applied", result
    # confirmed path never touches the gate.
    assert gs.get(gid).gate_status == "approved"
