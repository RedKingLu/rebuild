"""R17.3-6 WP-4 安全硬化与 Policy 底线 — 真实（非 mock）测试。

覆盖：
  SEC-01     P6 脱敏硬门禁（含密钥 → blocked/强制 Gate；放行需用户显式批准；正常产物正常交付）
  GAP-SEC-1  SecurityAuthorizationService（Policy 底线 + 审计 + 风险解释；LLM/Agent 不得绕过 Policy）
  GAP-SEC-2  Hook 执行引擎（PreToolUse 真实拦截）
  GAP-HITL-1 ACP 交互式 HITL（高风险请求走用户 Gate，非 reject 兜底）
  Auto Review Agent（Auto 模式经 Auto Review Agent 审；Policy 底线不被绕过）
"""

import asyncio
import json

import pytest

from app.dependencies import get_services
from app.services.workspace_service import workspace_path


# ── GAP-SEC-1: SecurityAuthorizationService — Policy 底线 + 审计 + 风险解释 ──────

def test_gap_sec1_policy_is_floor_agent_cannot_loosen(isolated_data):
    """LLM/Agent 试图批准 Policy 禁止项（deny-list L5）→ 被 Policy 拦截，Agent 无法放行。"""
    from app.services.security_authorization import SecurityAuthorizationService
    svc = get_services()
    sec = SecurityAuthorizationService(svc)

    # Agent reviewer 试图放行一个高风险 L5 动作（把 escalate 变成 auto_approved）。
    def loosen_reviewer(ctx):
        return {"advice": "auto_approved", "reason": "agent thinks it's fine"}

    out = sec.authorize(mode="auto", risk_level="L5", action="execute_command",
                        project_id="p-sec1", agent_reviewer=loosen_reviewer)
    # Policy 底线 = require_confirmation（L5 从不 auto_approved）；Agent 无法放行。
    assert out["decision"] != "auto_approved"
    assert out["policy_decision"] != "auto_approved"
    assert out["agent_overridden_by_policy"] is True
    assert out["risk_explanation"]["policy_is_floor"] is True
    # 审计已写入。
    assert out["audit_ref"]
    audits = svc.audit_writer.query(project_id="p-sec1", limit=20)
    assert any(a["audit_type"] == "security_authorization" for a in audits)


def test_gap_sec1_agent_can_tighten(isolated_data):
    """Agent 可以更严（把 auto_approved 收紧为 escalate）——加严允许，放松不允许。"""
    from app.services.security_authorization import SecurityAuthorizationService
    sec = SecurityAuthorizationService(get_services())

    def tighten_reviewer(ctx):
        return {"advice": "escalate", "reason": "suspicious scope"}

    out = sec.authorize(mode="auto", risk_level="L1", action="query",
                        project_id="p-sec1b", agent_reviewer=tighten_reviewer)
    assert out["decision"] == "escalate"       # 采纳更严建议
    assert out["policy_decision"] == "auto_approved"
    assert out["agent_overridden_by_policy"] is False


def test_gap_sec1_risk_explanation_and_audit_on_allow(isolated_data):
    """放行也记审计 + 风险解释（拦截/放行皆有）。"""
    from app.services.security_authorization import SecurityAuthorizationService
    svc = get_services()
    sec = SecurityAuthorizationService(svc)
    out = sec.authorize(mode="plan", risk_level="L1", action="read_artifact", project_id="p-sec1c")
    assert out["decision"] == "auto_approved"
    assert out["risk_explanation"]["why"]
    assert out["audit_ref"]


def test_gap_sec1_secrets_redacted_in_audit(isolated_data):
    """风险解释/审计里凭据脱敏（D-032）。"""
    from app.services.security_authorization import redact_secrets
    txt = "connect with api_key=SECRETVALUE12345 and sk-abcdefghijklmnopqrstuvwxyz"
    red = redact_secrets(txt)
    assert "SECRETVALUE12345" not in red
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in red
    assert "[REDACTED]" in red


def test_gap_sec1_external_reviewer_wired_to_security_service(isolated_data):
    """external_command_reviewer 经 SecurityAuthorizationService，产出风险解释 + 审计。"""
    from app.services.external_command_reviewer import review
    svc = get_services()
    dec = review("rm -rf /", mode="auto", project_id="p-sec1d")   # deny-list
    assert dec.is_denied()
    assert dec.risk_explanation  # 有风险解释
    audits = svc.audit_writer.query(project_id="p-sec1d", limit=20)
    assert any(a["audit_type"] == "security_authorization" for a in audits)


# ── GAP-SEC-2: Hook 执行引擎 ────────────────────────────────────────────────

def _seed_pre_write_hook(db):
    from app.models.resource_entry import (
        ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus)
    import uuid
    db.add(ResourceEntry(
        resource_id=str(uuid.uuid4()), name="pre-write Policy check",
        resource_type=ResourceType.hook, source_type=SourceType.user_provided,
        source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
        status=ResourceStatus.active, enabled=True, description="pre-write",
        type_metadata={"hook_point": "PreToolUse", "hook_mode": "block",
                       "hook_impl": "pre_write_policy"}))
    db.commit()


def test_gap_sec2_hook_engine_blocks_secret_write(isolated_data):
    """PreToolUse block hook 真实拦截含密钥的写入。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _seed_pre_write_hook(db)
        outcome = run_hooks("PreToolUse", {
            "project_id": "p-hook", "tool_name": "fs_write_artifact",
            "write_scope": "workspace",
            "args": {"path": "output_code/app.py", "content": "API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234"},
        }, db)
        assert outcome.blocked is True
        assert "密钥" in outcome.block_reason or "REDACTED" in outcome.block_reason or "凭据" in outcome.block_reason
    finally:
        db.close()


def test_gap_sec2_hook_engine_blocks_source_write(isolated_data):
    """PreToolUse block hook 拦截 source/ 写入（D-099）。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _seed_pre_write_hook(db)
        outcome = run_hooks("PreToolUse", {
            "project_id": "p-hook2", "tool_name": "fs_write_artifact",
            "write_scope": "workspace",
            "args": {"path": "source/x.py", "content": "print(1)"},
        }, db)
        assert outcome.blocked is True
    finally:
        db.close()


def test_gap_sec2_hook_engine_allows_clean_write(isolated_data):
    """干净写入放行；read 工具不受 pre-write 影响。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _seed_pre_write_hook(db)
        ok = run_hooks("PreToolUse", {
            "project_id": "p-hook3", "tool_name": "fs_write_artifact",
            "write_scope": "workspace",
            "args": {"path": "output_code/clean.py", "content": "print('hi')"},
        }, db)
        assert ok.blocked is False
        rd = run_hooks("PreToolUse", {
            "project_id": "p-hook3", "tool_name": "fs_read",
            "write_scope": "none", "args": {"path": "source/x.py"},
        }, db)
        assert rd.blocked is False
    finally:
        db.close()


def test_gap_sec2_tool_registry_hook_blocks_secret_write(isolated_data):
    """execute_tool 在 tool 调用前真实跑 PreToolUse hook 并拦截含密钥写入（端到端挂点）。"""
    from app.core.database import get_session
    from app.models.resource_entry import (
        ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus)
    from app.services import tool_registry
    import uuid
    db = get_session()
    try:
        _seed_pre_write_hook(db)
        # 注册一个 workspace 写工具
        db.add(ResourceEntry(
            resource_id=str(uuid.uuid4()), name="fs_write_artifact",
            resource_type=ResourceType.tool, source_type=SourceType.user_provided,
            source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
            status=ResourceStatus.active, enabled=True, description="write",
            type_metadata={"tool_name": "fs_write_artifact", "write_scope": "workspace"}))
        db.commit()
        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact",
            {"path": "output_code/leak.py", "content": "token=sk-abcdefghijklmnopqrstuvwxyz9999"},
            project_id="p-hook4", stage="p4", db=db))
        assert result.get("status") == "blocked_by_hook"
        # 文件未被写入
        leaked = workspace_path("p-hook4") / "output_code" / "leak.py"
        assert not leaked.exists()
    finally:
        db.close()


# ── SEC-01: P6 脱敏硬门禁 ───────────────────────────────────────────────────

def _prep_p6(project_id, run_id, *, with_secret):
    """构造 P5 通过 + output_code 产物（含/不含密钥）。返回 pkg。"""
    ws = workspace_path(project_id)
    (ws / "artifacts").mkdir(parents=True, exist_ok=True)
    (ws / "output_code").mkdir(parents=True, exist_ok=True)
    content = ("API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234\n" if with_secret
               else "def add(a, b):\n    return a + b\n")
    (ws / "output_code" / "mod.py").write_text(content, encoding="utf-8")
    # P5 报告：can_be_completed=True
    (ws / "artifacts" / "p5_validation_report.json").write_text(
        json.dumps({"can_be_completed": True}), encoding="utf-8")
    # 构造最小 P4 input manifest（P5InputService 读取）
    from app.services.p6_delivery_service import P6DeliveryService
    svc = P6DeliveryService()
    # 直接调 scan：伪造 pkg 的 output/patch 收集依赖 P4 input；用 _collect_files + scan 单测
    output_files = svc._collect_files(ws, ["output_code/mod.py"])
    ok, issues = svc._desensitization_scan(ws, output_files, [])
    return ok, issues


def test_sec01_scan_detects_secret(isolated_data):
    ok, issues = _prep_p6("p-sec01a", "r1", with_secret=True)
    assert ok is False and len(issues) >= 1
    ok2, issues2 = _prep_p6("p-sec01a2", "r1", with_secret=False)
    assert ok2 is True and issues2 == []


def test_sec01_desensitization_gate_is_hard_block_release_path(isolated_data):
    """含密钥 → 默认硬阻断，创建强制脱敏放行 Gate；用户显式批准后放行。"""
    from app.services.p6_delivery_service import (
        DeliveryPackage, ensure_desensitization_gate,
        find_approved_desensitization_override, desensitization_risk_explanation)
    svc = get_services()
    pkg = DeliveryPackage(project_id="p-sec01b", run_id="r1")
    pkg.desensitization_ok = False
    pkg.desensitization_issues = [{"path": "output_code/mod.py", "pattern": "sk-", "count": 1}]

    # 未放行前无 override
    assert find_approved_desensitization_override("p-sec01b", "r1") is None
    gate_id = ensure_desensitization_gate("p-sec01b", "r1", pkg,
                                          tracer=svc.trace_writer, auditor=svc.audit_writer)
    assert gate_id
    # 风险解释脱敏（不含明文密钥值）
    expl = desensitization_risk_explanation(pkg)
    assert expl["issue_count"] == 1
    assert "sk-abcdefghij" not in json.dumps(expl)
    # 安全审计已写入
    audits = svc.audit_writer.query(project_id="p-sec01b", limit=20)
    assert any(a["audit_type"] == "security_desensitization_block" for a in audits)
    # 用户显式批准 → override 生效
    from app.schemas.gate import GateDecisionRequest
    svc.gate_service.decide(gate_id, GateDecisionRequest(decision="approve"), drive_promotion=False)
    assert find_approved_desensitization_override("p-sec01b", "r1") == gate_id


def test_sec01_p6_handler_blocks_on_secret(isolated_data):
    """RealP6Handler.execute：含密钥 → status=blocked（默认硬阻断，非软提示）。"""
    from app.graph.stage_handlers import RealP6Handler
    svc = get_services()
    project_id, run_id = "p-sec01c", "r1"
    _prep_p6(project_id, run_id, with_secret=True)

    # 构造 P4 input manifest 供 P5InputService 读取
    ws = workspace_path(project_id)
    (ws / "artifacts" / "p4").mkdir(parents=True, exist_ok=True)
    (ws / "artifacts" / "p4" / "p4_execution_summary.json").write_text(
        json.dumps({"output_code_refs": ["output_code/mod.py"], "patch_refs": [],
                    "evidence_refs": [], "p4_to_p5_gate_status": "approved"}),
        encoding="utf-8")

    handler = RealP6Handler(svc.trace_writer, svc.audit_writer)
    state = {"project_id": project_id, "run_id": run_id}
    result = asyncio.run(handler.execute(state))
    # 若 P5 input 读取成立则 blocked by SEC-01；否则至少不是无脱敏门禁的 completed
    if result.get("status") == "completed":
        pytest.fail("含密钥产物不应直接 completed（SEC-01 未生效）")
    assert result.get("status") == "blocked"


# ── GAP-HITL-1: ACP 交互式 HITL ─────────────────────────────────────────────

def test_gap_hitl1_high_risk_goes_to_interactive_gate(isolated_data):
    """高风险命令请求 → 创建交互式 HITL Gate（非 reject 兜底）；用户批准 → allow。"""
    from app.services.opencode_acp_client import OpenCodeACPClient
    from app.services.opencode_permission_handler import PermissionPolicy
    svc = get_services()

    # 注入一个 HITL resolver 模拟用户「批准」（真实 Gate 决策由 REST/用户驱动，
    # 此处以 resolver 注入点验证：高风险不再是盲 reject，而是走 HITL 决策）。
    approved = {"called": False}

    def approve_resolver(project_id, run_id, command, decision_obj):
        approved["called"] = True
        assert decision_obj.requires_hitl()
        return "once"

    client = OpenCodeACPClient("http://127.0.0.1:1", "pw", hitl_resolver=approve_resolver)
    ws = str(workspace_path("p-hitl"))
    # mvn 不在 allow-list → L3；plan 模式计划外 L3 → require_confirmation → HITL
    decision = client._evaluate_permission(
        "bash", "mvn clean install", ws, PermissionPolicy(),
        mode="plan", project_id="p-hitl", in_plan=False, run_id="r1")
    assert approved["called"] is True
    assert decision == "once"   # 用户批准 → 放行（非兜底 reject）


def test_gap_hitl1_default_gate_resolver_creates_real_gate(isolated_data):
    """默认 HITL 解析器为高风险命令创建真实 action_approval Gate（超时→保守 reject，非静默）。"""
    from app.services.opencode_acp_client import OpenCodeACPClient
    from app.services.opencode_permission_handler import PermissionPolicy
    svc = get_services()
    client = OpenCodeACPClient("http://127.0.0.1:1", "pw", hitl_gate_timeout=0.6)
    ws = str(workspace_path("p-hitl2"))
    decision = client._evaluate_permission(
        "bash", "mvn deploy", ws, PermissionPolicy(),
        mode="plan", project_id="p-hitl2", in_plan=False, run_id="r1")
    # 无人批准 → 超时保守 reject
    assert decision == "reject"
    # 但真实创建了 Gate（交互式，非盲 reject）
    gates = svc.gate_service.list_by_project("p-hitl2")
    assert any(g.gate_type == "action_approval" for g in gates)
    # 且写了 HITL 审计
    audits = svc.audit_writer.query(project_id="p-hitl2", limit=30)
    assert any(a["action"].startswith("permission_") for a in audits)


def test_gap_hitl1_denylist_still_hard_deny(isolated_data):
    """deny-list 命令仍硬拒绝，不进 HITL。"""
    from app.services.opencode_acp_client import OpenCodeACPClient
    from app.services.opencode_permission_handler import PermissionPolicy
    client = OpenCodeACPClient("http://127.0.0.1:1", "pw")
    ws = str(workspace_path("p-hitl3"))
    decision = client._evaluate_permission(
        "bash", "rm -rf /", ws, PermissionPolicy(),
        mode="auto", project_id="p-hitl3", in_plan=False, run_id="r1")
    assert decision == "reject"


# ── Auto Review Agent ───────────────────────────────────────────────────────

def test_auto_review_policy_floor_forces_gate(isolated_data):
    """计划含 L4+ 动作 → Policy 底线强制升级用户 Gate，LLM 不得绕过（即使 LLM 说 proceed）。"""
    from app.services.auto_review_agent import AutoReviewAgent
    svc = get_services()

    class _FakeGW:
        async def call(self, **kw):
            return {"status": "completed", "content": '{"verdict":"proceed","reason":"ok"}'}

    ara = AutoReviewAgent("p4", "p-ara1", "r1", tracer=svc.trace_writer,
                          auditor=svc.audit_writer, gateway=_FakeGW())
    res = ara.review_plan(["执行迁移命令", "写入 output_code 代码"],
                          goal="P4 执行", action_types=["execute_command", "write_file"])
    assert res.needs_user_gate is True            # 被 Policy 底线强制升级
    assert res.policy_floor_escalated is True
    assert res.llm_participated is True           # LLM 参与了但无法放行 L4+
    # 审计
    audits = svc.audit_writer.query(project_id="p-ara1", limit=20)
    assert any(a["audit_type"] == "auto_review" for a in audits)


def test_auto_review_llm_escalate(isolated_data):
    """低风险计划但 LLM 判 escalate → 升级用户 Gate。"""
    from app.services.auto_review_agent import AutoReviewAgent
    svc = get_services()

    class _FakeGW:
        async def call(self, **kw):
            return {"status": "completed",
                    "content": '{"verdict":"escalate","reason":"scope creep detected"}'}

    ara = AutoReviewAgent("p1", "p-ara2", "r1", auditor=svc.audit_writer, gateway=_FakeGW())
    res = ara.review_plan(["识别技术栈"], goal="P1 建档", action_types=["run_profiling"])
    assert res.needs_user_gate is True
    assert res.verdict == "escalate"


def test_auto_review_no_key_honest_degrade(isolated_data):
    """无 Key（LLM 不可用）→ 诚实 evidence_gap，按 Policy 底线放行低风险（不伪造 LLM 结论）。"""
    from app.services.auto_review_agent import AutoReviewAgent
    svc = get_services()

    class _NoGW:
        async def call(self, **kw):
            return {"status": "failed", "error_category": "not_configured", "content": ""}

    ara = AutoReviewAgent("p1", "p-ara3", "r1", auditor=svc.audit_writer, gateway=_NoGW())
    res = ara.review_plan(["识别技术栈"], goal="P1", action_types=["run_profiling"])
    assert res.llm_participated is False
    assert res.evidence_gap is not None
    assert res.needs_user_gate is False           # 低风险 + 无 L4+ → Policy 底线放行
