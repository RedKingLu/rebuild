"""B-ACC-GATE-APPROVAL-NOT-BOUND 回归锁：action_approval 的批准必须绑定被审阅的入参。

台账：`B-ACC-GATE-APPROVAL-NOT-BOUND`（**P1**，4 条解除条件）。

原缺陷（`tool_registry._resolve_action_gate` 的 `_matches`）：
    blob = f"{g.reason or ''} {g.summary or ''}"
    return tool_name in blob        # ← 只比工具名，无任何入参比对
后果是**机制层的、不是偶发**：用户看到入参 X 并批准，该 Gate 转 approved 后一直挂账，
直到本 run 内**下一次**同名工具调用把它消费掉，而那次调用的入参是当时新传入的 Y
⇒ **人工签核的是 X，实际授权的是 Y**。它直接削弱 `B-R20-GATE-NO-PAYLOAD` 那次修复的
意图（把入参写进 Gate 是为了让审批"知情"）—— **知情的对象与授权的对象不是同一个东西**。

本文件锁定（编号对应 ③ 完善方案 §3 的 T9~T12）：
  T9  同 run 同工具、**入参不同** ⇒ 不复用已批准 Gate（重开新 Gate 请人重新审批）；
  T10 同 run 同工具、**入参相同** ⇒ 正常复用已批准 Gate（不因过严而反复弹 Gate）；
  T11 指纹与 Gate 展示用**同一份**规范化结果（键序不同 / 含非 ASCII 仍匹配）；
  T12 请求执行**不在白名单**的命令（`find …`）⇒ 直接 blocked，且**未创建任何 Gate**。

另附边界锁：
  · 指纹缺失（历史 Gate / 非工具类 action_approval Gate）⇒ **不授权**（fail-closed）；
  · 规范化【不做】大小写折叠与空白折叠（刻意偏"规范化不足"一侧：宁可多弹一次 Gate，
    不可把两条语义不同的命令认成同一条）；
  · 比对不可关闭：`_resolve_action_gate` 内不存在 enabled 开关 / 环境变量旁路 /
    try-except 静默跳过（以源码断言锁定，防止日后被"临时加个开关"绕开）。
"""

import inspect

import pytest  # noqa: F401  (pytest-asyncio auto mode collects async tests)

from app.core.database import get_session
from app.dependencies import get_services
from app.models.gate import Gate
from app.models.resource_entry import (
    ResourceEntry, ResourceType, ResourceStatus, RiskLevel,
)
from app.services import tool_registry
from app.services.tool_registry import action_args_fingerprint
from app.services.workspace_service import init_workspace


_PID = "proj-acc-fp"
_RUN = "run-acc-fp"
_TOOL = "run_safe_command"


def _seed_run_safe_command():
    """注册 L4 的 run_safe_command（与 seed.py:204-210 的真实注册项同形）。"""
    db = get_session()
    try:
        db.add(ResourceEntry(
            resource_type=ResourceType.tool,
            name="Run Safe Command",
            description="运行白名单内安全命令（L4，过 Gate）",
            risk_level=RiskLevel.L4,
            status=ResourceStatus.active,
            enabled=True,
            # 与真实注册项一致：run_safe_command 不声明 write_scope（该缺失属
            # B-ACC-NO-READONLY-FILEGLOB 解除条件②，归批次三，本批次不动）
            type_metadata={"tool_name": _TOOL},
        ))
        db.commit()
    finally:
        db.close()


def _approved_gate_for(args: dict | None, tool_name: str = _TOOL) -> str:
    """建一个【已批准】的 action_approval Gate，绑定 args 的指纹。

    指纹用生产同一个函数计算（`action_args_fingerprint`），不在测试里另算一份 ——
    否则测试会变成"我以为的指纹"而不是"生产真的会算出的指纹"。
    """
    gs = get_services().gate_service
    gate = gs.create(
        project_id=_PID, run_id=_RUN, stage="p4",
        gate_type="action_approval", risk_level="L4",
        reason=f"高风险工具 {tool_name}（风险 L4）执行前需人工审批",
        summary=f"Agent 拟执行高风险工具 {tool_name}（L4），请审批。",
        options=["approve", "reject"],
        action_fingerprint=action_args_fingerprint(tool_name, args),
    )
    db = get_session()
    try:
        g = db.get(Gate, gate.gate_id)
        g.gate_status = "approved"      # 直接置 approved，避开晋级副作用
        db.commit()
    finally:
        db.close()
    return gate.gate_id


def _gate_count() -> int:
    db = get_session()
    try:
        return db.query(Gate).filter(Gate.project_id == _PID).count()
    finally:
        db.close()


async def _run_tool(args: dict):
    db = get_session()
    try:
        return await tool_registry.execute_tool(
            _TOOL, args, _PID, stage="p4", db=db, run_id=_RUN)
    finally:
        db.close()


# ── T9：入参不同 ⇒ 不复用已批准 Gate（修复本体）──────────────────────────

async def test_t9_different_args_do_not_reuse_approved_gate():
    """T9：用户批准的是 `echo approved-one`，Agent 改执行 `echo something-else`
    ⇒ 不得复用那次批准；必须重开新 Gate 请人重新审批，且原 Gate 仍是 approved 未被消费。

    旧代码在此会直接放行执行 —— 人工签核的是 X，实际执行的是 Y。"""
    init_workspace(_PID)
    _seed_run_safe_command()
    approved_args = {"command": "echo approved-one"}
    gid = _approved_gate_for(approved_args)
    before = _gate_count()

    result = await _run_tool({"command": "echo something-else"})

    assert result["status"] == "awaiting_approval", (
        f"入参与被批准的那份不一致，却未拦下（缺陷复发）：{result}")
    assert result["gate_id"] != gid, "不得复用为另一份入参签发的授权"
    assert _gate_count() == before + 1, "应新开一个 Gate 请人重新审批"
    assert get_services().gate_service.get(gid).gate_status == "approved", \
        "原 Gate 不该被这次不匹配的调用消费掉"


async def test_t9b_same_key_different_value_is_not_a_match():
    """T9 附：只改一个参数值（同一个键）也必须重新审批 —— 比对的是内容，不是形状。"""
    init_workspace(_PID)
    _seed_run_safe_command()
    gid = _approved_gate_for({"command": "echo one", "cwd": "source"})

    result = await _run_tool({"command": "echo one", "cwd": "output_code"})

    assert result["status"] == "awaiting_approval", result
    assert result["gate_id"] != gid


# ── T10：入参相同 ⇒ 正常复用（不因过严而反复弹 Gate）───────────────────

async def test_t10_identical_args_reuse_approved_gate_and_execute():
    """T10：入参与被批准的那一份完全相同 ⇒ 授权成立，真实执行（不反复弹 Gate）。

    这条是 R-2 风险（指纹过严 ⇒ 同一动作反复弹 Gate）的锁。"""
    init_workspace(_PID)
    _seed_run_safe_command()
    args = {"command": "echo approved-one"}
    _approved_gate_for(args)
    before = _gate_count()

    result = await _run_tool(dict(args))

    assert result.get("status") != "awaiting_approval", (
        f"入参与批准的完全相同却仍要求再审批（过严，会导致反复弹 Gate）：{result}")
    assert result.get("blocked") is not True, result
    assert "approved-one" in (result.get("stdout") or ""), \
        f"授权成立后应真实执行并拿到 stdout：{result}"
    assert _gate_count() == before, "复用既有授权时不得新建 Gate"


# ── T11：指纹与展示同源（解除条件②点名的坑）────────────────────────────

async def test_t11_key_order_and_non_ascii_still_match():
    """T11：键序不同 + 含非 ASCII 值 ⇒ 仍匹配（规范化 = 键排序 + 紧凑分隔符，同一份实现）。

    解除条件②：指纹须用与展示同源的规范化结果，否则会出现"展示脱敏后、比对未脱敏"
    导致永不匹配、反复弹 Gate。"""
    init_workspace(_PID)
    _seed_run_safe_command()
    approved_args = {"command": "echo 中文参数", "note": "阶段 p4", "n": 1}
    _approved_gate_for(approved_args)
    before = _gate_count()

    # 同一份入参，键序完全不同
    result = await _run_tool({"n": 1, "note": "阶段 p4", "command": "echo 中文参数"})

    assert result.get("status") != "awaiting_approval", (
        f"键序不同导致指纹不匹配 ⇒ 规范化未做键排序（会反复弹 Gate）：{result}")
    assert _gate_count() == before


def test_t11b_fingerprint_and_display_share_one_canonicalizer():
    """T11 附：展示渲染器与指纹**调用同一个规范化函数**（源码级锁定）。

    只断言"两处都调用 `_canonical_action_args_json`"，不断言具体文本 —— 这样规范化算法
    日后若调整，本锁仍能保证两处不会各自漂移（那正是解除条件②要防的事）。"""
    display_src = inspect.getsource(tool_registry._redacted_action_payload)
    fp_src = inspect.getsource(tool_registry.action_args_fingerprint)
    assert "_canonical_action_args_json" in display_src, \
        "Gate 展示未走共享规范化函数（会与指纹漂移）"
    assert "_canonical_action_args_json" in fp_src, \
        "指纹未走共享规范化函数（会与展示漂移）"


def test_t11c_normalization_does_not_fold_case_or_whitespace():
    """T11 附：规范化【不做】大小写折叠 / 空白折叠 —— 刻意偏"规范化不足"一侧。

    `rm  -rf` 与 `rm -rf`、`ECHO` 与 `echo` 在 shell 语义上并不总等价，把它们折叠成同一
    指纹等于"少拦一次"。宁可多弹一次 Gate。"""
    a = action_args_fingerprint("t", {"command": "echo x"})
    b = action_args_fingerprint("t", {"command": "echo  x"})     # 双空格
    c = action_args_fingerprint("t", {"command": "ECHO x"})      # 大写
    assert a != b, "空白被折叠了（规范化过度）"
    assert a != c, "大小写被折叠了（规范化过度）"


def test_t11d_tool_name_and_args_cannot_collide():
    """T11 附：工具名与入参用 \\x00 分隔 ⇒ 拼接歧义不可能发生。"""
    assert (action_args_fingerprint("ab", {"c": 1})
            != action_args_fingerprint("a", {"b": 1, "c": 1}))


# ── T12：建 Gate 前先过白名单（解除条件④）──────────────────────────────

async def test_t12_command_not_in_whitelist_blocks_without_creating_gate():
    """T12：请求执行不在白名单的命令（真跑现场那条 `find …`）⇒ 直接 blocked，
    **断言未创建任何 Gate**。

    真跑坐实：`gate-22343c` 请用户签核 `find source -name "*.dll" … | head -50`，
    而 `find` 不在 `ALLOWED_COMMANDS` ⇒ 即便批准也会被挡回。平台在请用户为一条注定
    无法执行的命令签核 —— 既浪费人工裁决成本，也会训练用户"反正批了也没事"的习惯。"""
    init_workspace(_PID)
    _seed_run_safe_command()
    before = _gate_count()

    result = await _run_tool(
        {"command": 'find source -name "*.dll" -o -name "*.csproj" | head -50'})

    assert result.get("blocked") is True, result
    assert result["stderr"] == "命令不在允许列表中: find", result
    assert result["status"] == "blocked_not_allowed", result
    assert _gate_count() == before, "注定被挡回的命令不得创建 Gate 请用户签核"


async def test_t12b_whitelisted_command_still_creates_gate():
    """T12 附：白名单内的命令仍照常创建 Gate 请人审批（预检不得把正常流程一并挡掉）。"""
    init_workspace(_PID)
    _seed_run_safe_command()
    before = _gate_count()

    result = await _run_tool({"command": "echo needs-approval"})

    assert result["status"] == "awaiting_approval", result
    assert result["gate_id"]
    assert _gate_count() == before + 1


def test_t12c_precheck_reuses_provider_whitelist_predicate():
    """T12 附：预检与真实执行**共用同一个首词判据**（源码级锁定，防两套解析漂移），
    且本批次未改动 ALLOWED_COMMANDS 的内容（`find` 仍不在其中——那归批次三）。"""
    from app.services import execution_provider as ep
    pre_src = inspect.getsource(tool_registry._precheck_command_allowed)
    run_src = inspect.getsource(ep._run_subprocess)
    assert "bash_whitelist_violation" in pre_src and "bash_whitelist_violation" in run_src
    assert ep.ALLOWED_COMMANDS == ["python3", "python", "echo", "cat", "ls", "pwd", "which"], \
        "本批次明确不改白名单内容（加 find 属 B-ACC-NO-READONLY-FILEGLOB，归批次三）"


# ── 边界锁：指纹缺失 / 不可关闭 ─────────────────────────────────────────

async def test_legacy_gate_without_fingerprint_does_not_authorize():
    """指纹缺失的已批准 Gate（改动前建的历史 Gate、或 routes_registry / ACP HITL 建的
    非工具类 action_approval Gate）**不授权**（fail-closed）。

    代价是升级后已批准未消费的旧 Gate 需重新审批一次；取"宁可多弹一次 Gate，
    不可少拦一次"。"""
    init_workspace(_PID)
    _seed_run_safe_command()
    gs = get_services().gate_service
    gate = gs.create(
        project_id=_PID, run_id=_RUN, stage="p4",
        gate_type="action_approval", risk_level="L4",
        reason=f"高风险工具 {_TOOL}（风险 L4）执行前需人工审批",
        summary=f"Agent 拟执行高风险工具 {_TOOL}（L4），请审批。",
        options=["approve", "reject"],
        # action_fingerprint 未传 → NULL（历史 Gate 的形态）
    )
    db = get_session()
    try:
        db.get(Gate, gate.gate_id).gate_status = "approved"
        db.commit()
    finally:
        db.close()

    result = await _run_tool({"command": "echo anything"})

    assert result["status"] == "awaiting_approval", (
        f"无指纹的历史 Gate 仍在授权（等于空白授权未被封）：{result}")
    assert result["gate_id"] != gate.gate_id


def test_fingerprint_check_has_no_off_switch():
    """比对不可关闭：`_resolve_action_gate` 源码内不得出现 enabled 开关 / 环境变量旁路 /
    把比对包进 try-except 静默跳过。

    沿用 `detect_protocol_leak`（tool_registry.py）确立的"安全检查不给开关 + fail-closed"
    模式。本条是源码级锁 —— 日后有人想"临时加个开关先让流程跑通"会在这里被拦下。"""
    src = inspect.getsource(tool_registry._resolve_action_gate)
    # 只扫**可执行代码**：docstring 与注释里本来就写着"无 enabled 开关"这类说明文字，
    # 连它们一起扫会把"文档说明"误判成"存在旁路"。
    body = src.split('"""')[2] if src.count('"""') >= 2 else src
    code_lines = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
    lowered = "\n".join(code_lines).lower()
    for forbidden in ("os.environ", "getenv", "enabled", "skip_fingerprint", "bypass"):
        assert forbidden not in lowered, f"授权指纹比对出现旁路迹象：{forbidden!r}"
    # 指纹计算不得被 except 吞掉（算不出来必须向上抛，不得退化为"不比对"）
    matches_body = src.split("def _matches", 1)[1]
    matches_code = "\n".join(ln for ln in matches_body.splitlines()
                             if not ln.strip().startswith("#"))
    assert "except" not in matches_code, "指纹比对被包进了 except（会 fail-open）"


def test_create_risk_gate_persists_fingerprint():
    """`_create_risk_gate` 必须把指纹写进 Gate（否则下一步取授权必然 fail-closed，
    表现为"批准了却还要再批一次"）。"""
    init_workspace(_PID)
    args = {"command": "echo persisted"}
    out = tool_registry._create_risk_gate(_PID, _RUN, "p4", "apply_patch_with_confirm",
                                         "L4", args=args)
    assert out["status"] == "awaiting_approval", out
    stored = get_services().gate_service.get(out["gate_id"])
    assert stored.action_fingerprint == action_args_fingerprint(
        "apply_patch_with_confirm", args)


def test_agent_loop_gate_uses_the_same_fingerprint_function():
    """`agent_loop._create_action_gate` 与 tool_registry 用**同一个**指纹函数。

    它建的 Gate 正是 `_resolve_action_gate` 会拿去授权 re-dispatch 的那一类；两处若各算
    一份，用户批准后工具仍会再弹一次 Gate（表现为"批了没用"）。"""
    from app.services import agent_loop as al
    src = inspect.getsource(al.AgentLoop._create_action_gate)
    assert "action_args_fingerprint" in src
    assert "action_fingerprint=" in src
