"""V26.2 返工批次二锁定测试：P 环节取消硬 token 预算（甲）+ P0 解析失败守卫（乙）。

对应台账两条 P0：
  · `B-V262-TOKENBUDGET-UNFIXED-4` —— 真实规模真跑时 P0/P2 的 LLM 输出被硬编码 `max_tokens`
    砍断（实测撞顶 16383 / 16384 / 16387）。用户 2026-09-16 裁决 Q-B2-1：**不再抬高天花板，
    直接取消 P 环节的平台侧硬预算**，上限交还给模型自身能力与既有时间护栏。
  · `B-ACC-P0-PARSEERROR-STILL-ACCEPTED` —— P0 识别产出 `parse_error` 后仍置
    `status="completed"`、阶段仍判 `accepted / issues: []`。与预算**完全独立**。

编号与施工计划 §5 的 U1~U16 一一对应（函数名带 uNN_ 前缀便于逐条核对）。

⚠ 本文件的能力边界（不得越读）：单元测试只能证明"请求体里没有 max_tokens 这个键"、"守卫在
   该拦的时候拦了"。它**不能**证明"不设上限后真实规模的 P0/P2 一定能产出完整结构化产物" ——
   那是台账解除条件 ④ 要求的**完整 MicroOA 真跑**，本文件不声称该条闭合。
   同理，本文件也不证明"截断风险已消除"：平台侧硬编码天花板已移除，但模型自身最大输出长度与
   三层时间护栏仍可能造成输出不完整（正因如此才要有截断诊断）。

不发起真实 LLM 调用：adapter 层用 monkeypatch 捕获 litellm 入参；服务层用注入的 fake gateway。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import workspace_service


_APP = Path(__file__).resolve().parents[1] / "app"
_SERVICES = _APP / "services"

# 施工计划 §1.3 A 组：P 阶段主推理调用（会产出结构化产物、会被截断）—— 取消硬预算的范围。
# 9 个文件 / 11 个调用点，完整覆盖 P0–P6。
_A_GROUP_FILES = [
    "intake_service.py",              # P0
    "profiling_service.py",           # P1 建档
    "acceptance_baseline_service.py",  # P1 验收基准（走 gw.call，非 tool loop）
    "tech_selection_service.py",      # P1→P2 选型
    "assessment_service.py",          # P2
    "planning_service.py",            # P3（2 个调用点）
    "p4_execution_worker.py",         # P4（2 个 call_stream 调用点）
    "p5_verification_agent.py",       # P5
    "p6_delivery_agent.py",           # P6
]


def _parse_module(name: str) -> ast.Module:
    return ast.parse((_SERVICES / name).read_text(encoding="utf-8"))


def _callee_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _max_tokens_kw(node: ast.Call):
    for kw in node.keywords:
        if kw.arg == "max_tokens":
            return kw.value
    return None


def _a_group_call_sites() -> list[tuple[str, str, ast.Call]]:
    """收集 A 组的主推理调用点（file, callee, node）。

    判据（精确到调用点而非文件）：A 组 9 个文件里所有经网关/循环发起模型调用的位置 ——
      · `run_stage_tool_loop(...)`  → 8 处（P0 / P1建档 / P1选型 / P2 / P3×2 / P5 / P6）
      · `call_stream(...)`          → 2 处（P4 工具循环两处）
      · `call(...)`                 → 3 处（P1 验收基准；P3 边提议；P4 legacy 单次生成）
    共 13 处。其中后两处（P3 边提议 2048 / P4 legacy 生成 4096）原属 B 组待评估，本批次评估
    结论为**纳入**（见 U16）。
    """
    sites: list[tuple[str, str, ast.Call]] = []
    for name in _A_GROUP_FILES:
        tree = _parse_module(name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee_name(node)
            if callee in ("run_stage_tool_loop", "call_stream", "call"):
                sites.append((name, callee, node))
    return sites


# ══════════════════════════════════════════════════════════════════════════════
# U1 · 源码级锁：A 组调用点不得传硬编码整数 max_tokens（防后人回填）
# ══════════════════════════════════════════════════════════════════════════════

def test_u1_a_group_call_sites_pass_no_hardcoded_max_tokens():
    sites = _a_group_call_sites()
    assert len(sites) == 13, f"A 组主推理调用点应为 13 处，实测 {len(sites)}：" \
                             f"{[(f, c, n.lineno) for f, c, n in sites]}"
    offenders = []
    for fname, callee, node in sites:
        val = _max_tokens_kw(node)
        if val is None:
            continue  # 不传 → 走 run_stage_tool_loop / gateway 的 None 默认（不设上限）
        # 允许传"模块级 env 旋钮常量"（Name），因为旋钮默认 None（不设上限）；
        # 禁止传字面量数字（那就是又一个平台侧硬编码天花板）。
        if isinstance(val, ast.Constant) and isinstance(val.value, (int, float)):
            offenders.append((fname, callee, node.lineno, val.value))
        elif not isinstance(val, (ast.Name, ast.Attribute)):
            offenders.append((fname, callee, node.lineno, ast.dump(val)[:60]))
    assert not offenders, f"P 阶段主推理调用点出现硬编码输出上限：{offenders}"


def test_u1b_all_nine_a_group_knobs_default_to_unset():
    """9 个 A 组站点的输出上限**默认全部为不设**（Q-B2-4「包含」：含已修好的站点，无例外）。"""
    from app.services import (acceptance_baseline_service, assessment_service, intake_service,
                              p4_execution_worker, p5_verification_agent, p6_delivery_agent,
                              planning_service, profiling_service, tech_selection_service)
    actual = {
        "P0 intake": intake_service._INTAKE_MAX_TOKENS,
        "P1 profiling": profiling_service._PROFILING_MAX_TOKENS,
        "P1 baseline": acceptance_baseline_service._BASELINE_MAX_TOKENS,
        "P1 tech_selection": tech_selection_service._TECHSEL_MAX_TOKENS,
        "P2 assessment": assessment_service._ASSESSMENT_MAX_TOKENS,
        "P3 planning": planning_service._PLANNING_MAX_TOKENS,
        "P4 gen": p4_execution_worker._GEN_MAX_TOKENS,
        "P5 verification": p5_verification_agent._P5_VERIFICATION_MAX_TOKENS,
        "P6 delivery": p6_delivery_agent._DELIVERY_MAX_TOKENS,
    }
    assert all(v is None for v in actual.values()), f"仍有站点带平台侧默认上限：{actual}"


def test_u1c_stage_loop_and_gateway_defaults_are_unset():
    """循环层与网关层默认值同为 None —— 否则 A 组"不传实参"会落到一个隐式天花板上。"""
    from app.services import model_gateway, stage_agent_loop
    assert inspect.signature(
        stage_agent_loop.run_stage_tool_loop).parameters["max_tokens"].default is None
    for fn in (model_gateway.ModelGateway.call, model_gateway.ModelGateway.call_stream):
        assert inspect.signature(fn).parameters["max_tokens"].default is None, fn


# ══════════════════════════════════════════════════════════════════════════════
# U2 / U3 / U5 · adapter 层：条件写入 + 逃生阀 + anthropic 协议回落
# ══════════════════════════════════════════════════════════════════════════════

class _FakeUsage:
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


class _FakeMsg:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResponse:
    def __init__(self, content='{"ok": true}'):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()


class _FakeStreamDelta:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None
        self.reasoning_content = None


class _FakeStreamChoice:
    def __init__(self, content):
        self.delta = _FakeStreamDelta(content)


class _FakeStreamChunk:
    def __init__(self, content=None, usage=None):
        self.choices = [_FakeStreamChoice(content)] if content is not None else []
        self.usage = usage


def _patch_litellm(monkeypatch, captured: dict):
    """捕获真正交给 litellm 的入参（非流式与流式共用同一个 acompletion 入口）。"""
    async def _stream():
        yield _FakeStreamChunk(content="ok")
        yield _FakeStreamChunk(content=None, usage=_FakeUsage())

    async def _fake_acompletion(**kwargs):
        captured.clear()
        captured.update(kwargs)
        if kwargs.get("stream"):
            return _stream()
        return _FakeResponse()

    async def _fake_aresponses(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return SimpleNamespace(output=[], usage={})

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
    monkeypatch.setattr("litellm.aresponses", _fake_aresponses)


@pytest.mark.asyncio
async def test_u2_request_body_has_no_max_tokens_key_when_unset(monkeypatch):
    """U2（甲 ① 新口径）：max_tokens=None ⇒ 请求体**不含**该键 —— 不是含 `max_tokens: None`。

    为什么必须锁"不含键"而非"含 None"：旧实现无条件写入该键，传 None 会把 `max_tokens: null`
    真的发出去，行为依赖 litellm 版本 / provider 实现（可能 400、可能当 0）。
    """
    from app.adapters.litellm_adapter import LiteLLMAdapter
    captured: dict = {}
    _patch_litellm(monkeypatch, captured)
    adapter = LiteLLMAdapter()
    msgs = [{"role": "user", "content": "hi"}]

    # ① 流式 chat completions（P0-P6 主路径）
    frames = [f async for f in adapter.stream_complete(
        model="openai/m", messages=msgs, api_base="https://x/v1", api_key="k")]
    assert "max_tokens" not in captured, f"流式请求体仍含该键：{sorted(captured)}"
    assert frames[-1]["type"] == "done"

    # ② 非流式 complete（P1 acceptance_baseline 走这条）
    r = await adapter.complete(model="openai/m", messages=msgs,
                               api_base="https://x/v1", api_key="k")
    assert r.status == "completed"
    assert "max_tokens" not in captured, f"非流式请求体仍含该键：{sorted(captured)}"

    # ③ Responses 通道（键名为 max_output_tokens）
    await adapter.complete_via_responses(model="openai/m", messages=msgs,
                                         api_base="https://x/v1", api_key="k")
    assert "max_output_tokens" not in captured
    _ = [f async for f in adapter.stream_via_responses(
        model="openai/m", messages=msgs, api_base="https://x/v1", api_key="k")]
    assert "max_output_tokens" not in captured


@pytest.mark.asyncio
async def test_u3_explicit_max_tokens_still_forwarded(monkeypatch):
    """U3：显式传值仍原样生效（逃生阀 —— 成本失控时运维/调用方仍能压一个上限）。"""
    from app.adapters.litellm_adapter import LiteLLMAdapter
    captured: dict = {}
    _patch_litellm(monkeypatch, captured)
    adapter = LiteLLMAdapter()
    msgs = [{"role": "user", "content": "hi"}]

    _ = [f async for f in adapter.stream_complete(
        model="openai/m", messages=msgs, api_base="https://x/v1", api_key="k",
        max_tokens=4321)]
    assert captured["max_tokens"] == 4321

    await adapter.complete(model="openai/m", messages=msgs, api_base="https://x/v1",
                           api_key="k", max_tokens=1234)
    assert captured["max_tokens"] == 1234

    await adapter.complete_via_responses(model="openai/m", messages=msgs,
                                         api_base="https://x/v1", api_key="k", max_tokens=777)
    assert captured["max_output_tokens"] == 777


@pytest.mark.asyncio
async def test_u4_env_knob_takes_effect_end_to_end(monkeypatch):
    """U4：env 旋钮设值时真的透传到调用；不设时调用侧为 None（请求体无该键，见 U2）。"""
    import app.services.profiling_service as ps

    captured: dict = {}

    async def _fake_loop(gw, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "content": "{}", "model_used": "fake-model"}

    class _ReadyGW:
        def get_status(self):
            return SimpleNamespace(overall_status="available")

        def stage_model_readiness(self, **kw):
            return {"available": True, "capability_ok": True, "reason": "",
                    "attempted_chain": [], "user_actions": []}

        async def call(self, **kw):
            return {"status": "completed", "model": "fake-model", "content": "{}"}

    monkeypatch.setattr("app.services.stage_agent_loop.run_stage_tool_loop", _fake_loop)

    # ① 不设 → None
    await ps.ProfilingService(gateway=_ReadyGW()).profile("p-u4", facts={"file_count": 1},
                                                          upstream={})
    assert captured["max_tokens"] is None

    # ② 设值 → 生效（reload 后还原，避免污染同进程其它用例）
    old = os.environ.get("P1_PROFILING_MAX_TOKENS")
    os.environ["P1_PROFILING_MAX_TOKENS"] = "5555"
    try:
        ps2 = importlib.reload(ps)
        captured.clear()
        await ps2.ProfilingService(gateway=_ReadyGW()).profile("p-u4b", facts={"file_count": 1},
                                                               upstream={})
        assert captured["max_tokens"] == 5555
    finally:
        if old is None:
            os.environ.pop("P1_PROFILING_MAX_TOKENS", None)
        else:
            os.environ["P1_PROFILING_MAX_TOKENS"] = old
        importlib.reload(ps)
    assert ps._PROFILING_MAX_TOKENS is None


def test_u4b_optional_int_env_rejects_garbage_loudly(caplog):
    """旋钮取值非法/<=0 时不得静默变成一个真实上限：按"不设"处理并发声（公理3）。"""
    from app.services.stage_agent_loop import optional_int_env
    os.environ["_B2_TEST_KNOB"] = "not-a-number"
    try:
        with caplog.at_level("WARNING"):
            assert optional_int_env("_B2_TEST_KNOB") is None
        assert any("_B2_TEST_KNOB" in r.getMessage() for r in caplog.records)
        os.environ["_B2_TEST_KNOB"] = "0"
        assert optional_int_env("_B2_TEST_KNOB") is None
        os.environ["_B2_TEST_KNOB"] = "  4096  "
        assert optional_int_env("_B2_TEST_KNOB") == 4096
        os.environ["_B2_TEST_KNOB"] = ""
        assert optional_int_env("_B2_TEST_KNOB") is None
    finally:
        os.environ.pop("_B2_TEST_KNOB", None)


@pytest.mark.asyncio
async def test_u5_anthropic_format_falls_back_to_explicit_ceiling(monkeypatch):
    """U5（§2.2 latent risk 防御）：anthropic 协议的 max_tokens 是**必填**，省略会 400。

    ⇒ api_format=="anthropic" 且未设上限时必须回落到显式上限，且回落原因必须可见
    （非流式：result.trace_data；流式：done/error 帧的 max_tokens_fallback）。
    既不得静默省略导致请求失败，也不得静默用一个无依据的数字。
    """
    from app.adapters import litellm_adapter as la
    captured: dict = {}
    _patch_litellm(monkeypatch, captured)
    adapter = la.LiteLLMAdapter()
    msgs = [{"role": "user", "content": "hi"}]
    expected = la._ANTHROPIC_FALLBACK_MAX_TOKENS
    assert isinstance(expected, int) and expected > 0

    # ① 非流式
    r = await adapter.complete(model="anthropic/m", messages=msgs, api_base="https://x/anthropic",
                               api_key="k", api_format="anthropic")
    assert captured["max_tokens"] == expected, "anthropic 协议下不得省略 max_tokens"
    fb = r.trace_data.get("max_tokens_fallback")
    assert fb and fb["applied"] is True and fb["value"] == expected
    assert "anthropic" in fb["reason"] and fb["env_knob"]

    # ② 流式
    frames = [f async for f in adapter.stream_complete(
        model="anthropic/m", messages=msgs, api_base="https://x/anthropic", api_key="k",
        api_format="anthropic")]
    assert captured["max_tokens"] == expected
    assert frames[-1].get("max_tokens_fallback", {}).get("applied") is True

    # ③ 显式传值时不回落（调用方的值优先）
    await adapter.complete(model="anthropic/m", messages=msgs, api_base="https://x/anthropic",
                           api_key="k", api_format="anthropic", max_tokens=999)
    assert captured["max_tokens"] == 999

    # ④ openai 协议不受该护栏影响（未设 ⇒ 仍然不含该键）
    await adapter.complete(model="openai/m", messages=msgs, api_base="https://x/v1", api_key="k")
    assert "max_tokens" not in captured


def test_u5b_time_guardrails_not_relaxed():
    """取消 token 预算后，三层时间护栏是仅剩的输出边界 —— 本批次不得放宽（计划 §4 明确不改）。"""
    from app.adapters import litellm_adapter as la
    assert la._REQUEST_TIMEOUT == 60.0, "chunk 间读超时/单次请求超时默认值不得放宽"
    assert la._STREAM_TOTAL_TIMEOUT == 240.0, "流式总时长上限默认值不得放宽"
    src = (_APP / "adapters" / "litellm_adapter.py").read_text(encoding="utf-8")
    # chunk 间读超时故意**不**随调用方 timeout 放大（原注释与实现须同时保持）。
    assert "inter_chunk_timeout = _REQUEST_TIMEOUT" in src


# ══════════════════════════════════════════════════════════════════════════════
# U6 / U7 / U8 / U9 / U10 · 共享截断诊断
# ══════════════════════════════════════════════════════════════════════════════

_TRUNCATED = '{"tech_stack": {"primary_language": "C#", "languages": [{"language": "C#"'
_TRUNCATED_MID_STRING = '{"tech_stack": {"primary_language": "C#", "path": "Resource/'
_MALFORMED = "{'tech_stack': 'single quotes are not valid json'}"
_COMPLETE = '{"tech_stack": {"primary_language": "C#"}}'


def test_u6_diagnosis_flags_truncated_output():
    from app.services.stage_agent_loop import diagnose_parse_failure
    diag = diagnose_parse_failure(_TRUNCATED, max_tokens=None, timeout_s=180.0,
                                  env_knobs="P0_INTAKE_MAX_TOKENS")
    assert diag["suspected_truncation"] is True
    assert diag["truncation_signals"], "截断信号不得为空"
    assert any("unbalanced_depth" in s for s in diag["truncation_signals"])
    assert any("tail_not_closed" in s for s in diag["truncation_signals"])
    assert diag["content_len"] == len(_TRUNCATED)

    # 停在字符串中途的形态（真实规模真跑实测就是这一种：raw 结尾断在 `"path": "Resource/` 半句）
    diag2 = diagnose_parse_failure(_TRUNCATED_MID_STRING)
    assert diag2["suspected_truncation"] is True
    assert any("unclosed_string" in s for s in diag2["truncation_signals"])

    # 空响应（推理链耗尽后无最终文本）同样归入疑似截断
    diag3 = diagnose_parse_failure("")
    assert diag3["suspected_truncation"] is True
    assert any("empty_content" in s for s in diag3["truncation_signals"])


def test_u7_diagnosis_does_not_misflag_complete_or_malformed_output():
    """结构已收敛的输出不得被误判为截断（否则会把"改 prompt 契约"误导成"加上限"）。"""
    from app.services.stage_agent_loop import diagnose_parse_failure
    for text in (_COMPLETE, _MALFORMED):
        diag = diagnose_parse_failure(text)
        assert diag["suspected_truncation"] is False, text
        assert diag["truncation_signals"] == []


def test_u8_diagnosis_reports_unset_ceiling_explicitly():
    """U8：未设上限时 max_tokens 字段显示"未设置（…）"而非 null —— 使现场看得出不是平台砍的。"""
    from app.services.stage_agent_loop import MAX_TOKENS_UNSET_DISPLAY, diagnose_parse_failure
    diag = diagnose_parse_failure(_TRUNCATED, max_tokens=None)
    assert diag["max_tokens"] == MAX_TOKENS_UNSET_DISPLAY
    assert diag["max_tokens"] is not None
    for kw in ("未设置", "模型", "超时"):
        assert kw in diag["max_tokens"]
    # 设了上限时如实回显数字（不能反过来把真实上限也说成"未设置"）
    assert diagnose_parse_failure(_TRUNCATED, max_tokens=16384)["max_tokens"] == 16384


def test_u9_truncation_diagnosis_has_exactly_one_implementation():
    """U9（防漂移）：算法只有一份实现，且 A 组 9 个文件都从共享模块取诊断/旋钮解析。

    历史教训：同一段诊断曾在 profiling / acceptance_baseline / p5_verification 里各存一份逐字
    副本，三份可以各自漂移，而"未修的四处连诊断都没有"正是本缺陷被误归因的直接原因。
    """
    marker = "unbalanced_depth="          # 算法内部信号字面量
    scan_def = "def scan_json_shape"      # 共享实现的定义
    hits = []
    for path in sorted(_APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if marker in text:
            hits.append(str(path.relative_to(_APP)))
    assert hits == ["services/stage_agent_loop.py"], f"诊断算法出现多份副本：{hits}"
    assert scan_def in (_SERVICES / "stage_agent_loop.py").read_text(encoding="utf-8")

    # A 组 9 个文件全部从 stage_agent_loop 取共享能力（诊断或旋钮解析）。
    for name in _A_GROUP_FILES:
        text = (_SERVICES / name).read_text(encoding="utf-8")
        assert "from app.services.stage_agent_loop import" in text, name


def test_u10_shared_diagnosis_is_behaviour_equivalent_to_previous_local_copies():
    """U10（R-4）：共享化后行为等价 —— 与提取前的实现（下面的参照实现）逐字段一致。

    参照实现是本次施工前 profiling_service._diagnose_parse_failure 的算法原样抄录（只用于
    比对，不进生产代码）。取同一批输入，比对两者输出必须完全相等。
    """
    from app.services.stage_agent_loop import diagnose_parse_failure, scan_json_shape

    def _ref_scan(text: str) -> tuple[int, bool]:
        depth, in_string, escaped = 0, False, False
        for ch in text:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
        return depth, in_string

    def _ref_diagnose(text: str, budget, timeout_s, knobs) -> dict:
        depth, in_string = _ref_scan(text)
        signals: list[str] = []
        if not text:
            signals.append("empty_content: 响应为空（可能推理链耗尽输出预算后无最终文本）")
        if in_string:
            signals.append("unclosed_string: 文本末尾停在未闭合的字符串内")
        if depth > 0:
            signals.append(f"unbalanced_depth={depth}: 有 {depth} 层 {{/[ 未闭合")
        if text and not text.rstrip().endswith(("}", "]")):
            signals.append("tail_not_closed: 末尾字符不是 } 或 ]")
        return {
            "content_len": len(text),
            "suspected_truncation": bool(signals),
            "truncation_signals": signals,
            "tail_snippet": text[-80:] if text else "",
            "max_tokens": budget,
            "timeout_s": timeout_s,
            "env_knobs": knobs,
        }

    samples = [_TRUNCATED, _MALFORMED, _COMPLETE, "", "   ", '{"a": "b\\"c"',
               '[{"a": 1}, {"b": ', 'prose then {"a": 1}', '{"a": 1}]']
    knobs = "P1_PROFILING_MAX_TOKENS / P1_PROFILING_TIMEOUT"
    for text in samples:
        assert scan_json_shape(text) == _ref_scan(text), text
        # 同一个上限取值下（32768 = 共享化前 profiling 的默认值），逐字段必须完全一致
        assert diagnose_parse_failure(text, max_tokens=32768, timeout_s=300.0,
                                      env_knobs=knobs) == _ref_diagnose(text, 32768, 300.0, knobs)


def test_u10b_all_p_stage_parse_paths_emit_diagnosis():
    """甲 ②：原先"完全没有截断诊断"的四处（P0/P1选型/P2/P6）现在都产出可诊断信息。"""
    from app.services.assessment_service import AssessmentService
    from app.services.intake_service import IntakeService
    from app.services.p6_delivery_agent import P6DeliveryAgent
    from app.services.tech_selection_service import TechSelectionService

    p0 = IntakeService()._parse(_TRUNCATED)
    assert p0["parse_error"] is True
    assert p0["parse_diagnosis"]["suspected_truncation"] is True
    assert "P0_INTAKE_MAX_TOKENS" in p0["parse_diagnosis"]["env_knobs"]

    p1 = TechSelectionService()._parse(_TRUNCATED)
    assert p1["parse_error"] is True and p1["parse_diagnosis"]["suspected_truncation"] is True

    p2 = AssessmentService()._parse(_TRUNCATED)["assessment_report"]
    assert p2["parse_error"] is True and p2["parse_diagnosis"]["suspected_truncation"] is True

    p6 = P6DeliveryAgent()._parse(_TRUNCATED)["delivery_narrative"]
    assert p6["parse_error"] is True and p6["parse_diagnosis"]["suspected_truncation"] is True


# ══════════════════════════════════════════════════════════════════════════════
# U11 / U12 / U13 · 乙：P0 解析失败守卫
# ══════════════════════════════════════════════════════════════════════════════

class _UnparseableGateway:
    """Key-present 但输出不可解析（无 call_stream ⇒ run_stage_tool_loop 走单次 call fallback）。"""

    def __init__(self, content=_TRUNCATED):
        self._content = content
        self.calls: list[dict] = []

    def get_status(self):
        return SimpleNamespace(overall_status="available")

    def stage_model_readiness(self, **kw):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, **kw):
        self.calls.append(kw)
        text = json.dumps(kw.get("messages"), ensure_ascii=False) if kw.get("messages") else ""
        if "verdict" in text:      # ValidationAgent 的 LLM 语义验收调用
            return {"status": "completed", "model": "m",
                    "content": json.dumps({"verdict": "accepted", "reason": "看起来还行"})}
        return {"status": "completed", "model": "fake-model", "content": self._content}


@pytest.mark.asyncio
async def test_u11_intake_does_not_report_completed_on_parse_error(isolated_data):
    """U11（乙 ①）：parsed 含 parse_error ⇒ **不得**返回 status="completed"。"""
    from app.services.intake_service import IntakeService
    svc = IntakeService(gateway=_UnparseableGateway())
    res = await svc.identify("p-u11", facts={"file_count": 3})
    assert res.status != "completed", "解析失败仍报 completed = 把不可用谎报为可用"
    assert res.status == "failed"
    assert "unparseable_output" in res.reason
    # 诚实：可诊断证据不得丢（含疑似截断判断），否则又会被误归因为"模型质量问题"
    assert res.identification.get("parse_error") is True
    assert res.identification["parse_diagnosis"]["suspected_truncation"] is True
    # 不得被误判为"模型全失败中断"（那会建 model_unavailable Gate、指错修复方向）
    assert not res.attempted_chain
    assert "no_model_key" not in res.reason


@pytest.mark.asyncio
async def test_u11b_intake_still_completes_on_parseable_output(isolated_data):
    """防过严：输出可解析时照旧 completed（守卫不得误伤正常项目）。"""
    from app.services.intake_service import IntakeService
    good = json.dumps({"primary_language": "Python", "detected_stack": ["Python"]})
    res = await IntakeService(gateway=_UnparseableGateway(good)).identify(
        "p-u11b", facts={"file_count": 3})
    assert res.status == "completed"
    assert res.identification["primary_language"] == "Python"


def test_u12_p0_review_flags_mass_empty_identification_keys():
    """U12（乙 ②）：识别键大面积为空 ⇒ 产出 issue 且 passed=False（不得以 issues:[] 掩盖）。"""
    from app.graph.stage_handlers import RealP0Handler
    h = RealP0Handler()
    rr = h.review({"status": "completed", "source_type": "git", "file_count": 12,
                   "identification": {"parse_error": True, "raw": _TRUNCATED,
                                      "parse_diagnosis": {"suspected_truncation": True,
                                                          "truncation_signals": ["unclosed_string"]}}})
    assert rr.passed is False
    types = [i["type"] for i in rr.issues]
    assert "p0_identification_major_gap" in types
    detail = next(i["detail"] for i in rr.issues if i["type"] == "p0_identification_major_gap")
    assert "12" in detail and "半数阈值" in detail
    assert "疑似截断=True" in detail, "截断线索须直接写在 issue 里（否则又要靠翻日志归因）"


def test_u12b_p0_review_threshold_is_same_source_as_p1_gate():
    """阈值口径取同源（半数），不另定数字 —— 与 P1 领域产物集完整性门控一致。"""
    from app.graph.stage_handlers import RealP0Handler
    from app.services.validation_agent import ValidationAgent
    fields = RealP0Handler._identification_fields()
    assert len(fields) == 12
    assert len(fields) // 2 == 6
    # P1 侧同源口径：8 项领域产物取半数 = 4
    assert ValidationAgent._P1_MAJOR_GAP_THRESHOLD == 4
    h = RealP0Handler()
    # 恰好差一项达到阈值（5/12 空）→ 不判失败；达到 6/12 → 判失败。
    def _ident(empty_n: int) -> dict:
        out = {}
        for i, k in enumerate(fields):
            out[k] = None if i < empty_n else ["real"]
        return out
    base = {"status": "completed", "source_type": "git", "file_count": 12}
    assert h.review({**base, "identification": _ident(5)}).passed is True
    assert h.review({**base, "identification": _ident(6)}).passed is False


def test_u13_p0_review_does_not_flag_healthy_identification():
    """U13（防过严）：识别键正常时不得产出该 issue（不误拦正常项目）。"""
    from app.graph.stage_handlers import RealP0Handler
    h = RealP0Handler()
    ident = {k: ["v"] for k in h._identification_fields()}
    rr = h.review({"status": "completed", "source_type": "git", "file_count": 12,
                   "identification": ident})
    assert rr.passed is True and rr.issues == []
    # 未带 identification 的调用方不受影响（产物缺失由 artifacts/独立结构核验负责）
    assert h.review({"status": "completed", "source_type": "git", "file_count": 12}).passed is True
    # 既有 empty_source 域规则不受影响（防回归）
    rr2 = h.review({"status": "completed", "source_type": "git", "file_count": 0})
    assert rr2.passed is False and rr2.issues[0]["type"] == "empty_source"


def test_u13b_p0_review_boundary_comment_present():
    """② 的边界必须写在代码注释里（防后人误删当越权、或误扩当可做质量判定）。"""
    src = (_APP / "graph" / "stage_handlers.py").read_text(encoding="utf-8")
    seg = src[src.index("class RealP0Handler"):src.index("class RealP1Handler")]
    for kw in ("确定性事实判断", "Acceptance Agent", "勿删勿扩"):
        assert kw in seg, kw


# ══════════════════════════════════════════════════════════════════════════════
# U14 · 端到端：不可解析 ⇒ 阶段不判 accepted，且**真的触发返工**
# ══════════════════════════════════════════════════════════════════════════════

def _seed_project(pid: str) -> None:
    workspace_service.init_workspace(pid)
    src = workspace_service.workspace_path(pid) / "source"
    (src / "app.py").write_text("import flask\nprint('x')\n", encoding="utf-8")
    (src / "requirements.txt").write_text("flask==2.0\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_u14_unparseable_p0_output_triggers_real_rework_not_accepted(isolated_data):
    """U14（乙 ①②③ / R-3）：端到端断言两件事 ——
      ① 阶段**不**判 `accepted / issues: []`；
      ② **真的触发返工**（ReviewPass 重跑了 WorkAgent，而不是从"静默通过"变成"静默卡住"）。
    """
    from app.graph.stage_handlers import RealP0Handler
    from app.graph.stage_loop import StageLoop
    from app.services.intake_service import IntakeService
    from app.services.source_materializer import generate_source_index
    from app.services.validation_agent import ValidationAgent
    from app.services.work_agent import WorkAgent

    pid = "b2-u14"
    _seed_project(pid)
    generate_source_index(pid, source_type="local_dir")

    gw = _UnparseableGateway()
    handler = RealP0Handler(intake_service=IntakeService(gateway=gw))
    wa = WorkAgent("p0", pid, run_id="r1", handler=handler)
    va = ValidationAgent("p0", pid, run_id="r1", handler=handler, gateway=gw)

    result = await wa.execute({"source_type": "manual", "project_id": pid})
    assert result["status"] != "completed", "P0 输出不可解析时不得声明完成"

    rr = va.validate(result)
    assert rr.passed is False, "独立验收不得判通过"
    assert rr.issues, "不得以 issues:[] 掩盖"
    assert va.last_result.verdict != "accepted", f"verdict={va.last_result.verdict}"

    # 落盘产物必须留下可诊断证据（读产物的人不必翻日志就能分辨"被截断"）
    intake = json.loads((workspace_service.workspace_path(pid) / "artifacts" / "p0"
                         / "intake_report.json").read_text("utf-8"))
    assert intake["identification"].get("parse_error") is True
    assert intake["identification"]["parse_diagnosis"]["suspected_truncation"] is True

    # ② 真的返工：ReviewPass 把 execute 重跑到轮次预算耗尽后才升级 Gate
    exec_rounds = {"n": 0}

    async def _execute():
        exec_rounds["n"] += 1
        return await wa.execute({"source_type": "manual", "project_id": pid})

    loop = StageLoop(pid, "p0", max_rounds=2, run_id="r1")
    res = await loop.run(goal=handler.goal, acceptance_criteria=list(handler.acceptance_criteria),
                         planned_actions=list(handler.planned_actions),
                         execute_fn=_execute, review_fn=va.validate)
    assert res.passed is False
    assert exec_rounds["n"] == 2, f"未真正返工重跑（execute 只跑了 {exec_rounds['n']} 次）"
    assert res.escalated_to_gate is True, "返工耗尽后须诚实升级为 Gate，而不是静默卡住"
    assert any(r.get("issues") for r in res.rounds)


@pytest.mark.asyncio
async def test_u15_mutation_each_guard_is_load_bearing(isolated_data, monkeypatch):
    """U15（变异验证）：逐个把守卫**改回缺陷原貌**，证明 U14 的绿不是空转来的。

    计划 §5 的 U15 原文是"删除 U11 的守卫 ⇒ U14 必须失败"。实测**不是这样**，如实记录：
    乙 ① 与乙 ② 是两条**相互独立**的守卫（这正是台账要求两条解除条件的原因），
      · 只还原 ①（intake 解析失败仍报 completed）⇒ 阶段**仍**不被判通过，因为 ② 从"识别键
        大面积为空"这条确定性事实上又拦了一次；
      · 只有把 ①② **同时**还原成缺陷原貌，阶段才会重现 `passed / accepted` 的谎报。
    ⇒ 下面分两级变异分别验证：①-only 证明 ② 不冗余；①+② 证明二者合起来正是 U14 变绿的原因。
    """
    from app.graph.stage_handlers import RealP0Handler
    from app.services.intake_service import IntakeService
    from app.services.review_pass import ReviewResult
    from app.services.source_materializer import generate_source_index
    from app.services.validation_agent import ValidationAgent
    from app.services.work_agent import WorkAgent

    # ── 变异级别 ①：还原"解析失败仍置 completed"（乙 ① 被删）──────────────────
    _orig_identify = IntakeService.identify

    async def _identify_without_guard(self, project_id, **kw):
        res = await _orig_identify(self, project_id, **kw)
        if res.status == "failed" and "unparseable_output" in res.reason:
            res.status = "completed"      # ← 缺陷原貌
            res.reason = ""
        return res

    monkeypatch.setattr(IntakeService, "identify", _identify_without_guard)

    pid_a = "b2-u15a"
    _seed_project(pid_a)
    generate_source_index(pid_a, source_type="local_dir")
    gw_a = _UnparseableGateway()
    handler_a = RealP0Handler(intake_service=IntakeService(gateway=gw_a))
    wa_a = WorkAgent("p0", pid_a, run_id="r1", handler=handler_a)
    va_a = ValidationAgent("p0", pid_a, run_id="r1", handler=handler_a, gateway=gw_a)
    res_a = await wa_a.execute({"source_type": "manual", "project_id": pid_a})
    assert res_a["status"] == "completed", "变异 ① 未生效则本级无意义"
    rr_a = va_a.validate(res_a)
    assert rr_a.passed is False, "①-only 变异下阶段仍须被 ② 拦住（否则 ② 是冗余的）"
    assert any(i.get("type") == "p0_identification_major_gap" for i in rr_a.issues), rr_a.issues

    # ── 变异级别 ①+②：再把 P0 review 还原成"只查 empty_source"的缺陷原貌 ──────
    def _review_pre_fix(self, result: dict) -> ReviewResult:
        issues = []
        if result.get("source_type") not in ("manual",) and result.get("file_count", 0) == 0:
            issues.append({"type": "empty_source", "detail": "非手动项目但源码目录为空"})
        return ReviewResult(passed=not issues, issues=issues, reviewer="p0_review_skill")

    monkeypatch.setattr(RealP0Handler, "review", _review_pre_fix)

    pid_b = "b2-u15b"
    _seed_project(pid_b)
    generate_source_index(pid_b, source_type="local_dir")
    gw_b = _UnparseableGateway()
    handler_b = RealP0Handler(intake_service=IntakeService(gateway=gw_b))
    wa_b = WorkAgent("p0", pid_b, run_id="r1", handler=handler_b)
    va_b = ValidationAgent("p0", pid_b, run_id="r1", handler=handler_b, gateway=gw_b)
    res_b = await wa_b.execute({"source_type": "manual", "project_id": pid_b})
    assert res_b["status"] == "completed"
    rr_b = va_b.validate(res_b)
    assert rr_b.passed is True, ("①+② 同时还原后阶段仍不通过 ⇒ U14 的绿与本批次两条守卫无关"
                                 f"（可能空转），须重新设计 U11/U12/U14。issues={rr_b.issues}")
    assert rr_b.issues == [], f"缺陷原貌应为 issues:[]（谎报），实测 {rr_b.issues}"
    assert va_b.last_result.verdict == "accepted", "缺陷原貌应重现 verdict=accepted"


# ══════════════════════════════════════════════════════════════════════════════
# U16 · 甲 ⑤ 顺带核查：B 组逐处结论 + C 组明确不在范围（结论须落成可执行的锁）
# ══════════════════════════════════════════════════════════════════════════════

def test_u16_b_group_promoted_sites_now_use_the_shared_knob():
    """B 组两处经评估**纳入**取消范围：P3 边提议（原 2048）、P4 legacy 单次生成（原 4096）。

    理由见各自 docstring（结构化产物 / 体量随项目增长 / 截断后果是静默降级或半截代码文件）。
    这里锁"确实改用了同阶段旋钮"而非又一个字面量。
    """
    planning_src = (_SERVICES / "planning_service.py").read_text(encoding="utf-8")
    p4_src = (_SERVICES / "p4_execution_worker.py").read_text(encoding="utf-8")
    assert "max_tokens=_PLANNING_MAX_TOKENS, temperature=0.2" in planning_src
    assert "max_tokens=_GEN_MAX_TOKENS" in p4_src
    # 用 AST 判"代码里"是否还有字面量上限（不能用字符串查找 —— 上面两处的 docstring 里就写着
    # 原值 2048 / 4096 作为历史留痕，字符串查找会把注释当成实现）。
    for fname in ("planning_service.py", "p4_execution_worker.py"):
        literals = []
        for node in ast.walk(_parse_module(fname)):
            if isinstance(node, ast.Call) and _callee_name(node) in (
                    "call", "call_stream", "run_stage_tool_loop"):
                val = _max_tokens_kw(node)
                if isinstance(val, ast.Constant) and isinstance(val.value, (int, float)):
                    literals.append((fname, node.lineno, val.value))
        assert not literals, f"仍残留字面量输出上限：{literals}"


def test_u16b_b_group_retained_sites_keep_small_budget_with_recorded_reason():
    """B 组两处经评估**保留**：auto_review_agent（512）、validation_agent 语义调用（512）。

    保留依据必须**写在代码里**（否则下一个窗口只会看到"又两处 512"而无法判断是设计还是遗漏）：
    定长小裁决、体量不随项目规模增长、失败方向为诚实 evidence_gap / fail-closed。
    """
    for fname, marker in (("auto_review_agent.py", "evidence_gap"),
                          ("validation_agent.py", "fail-closed")):
        text = (_SERVICES / fname).read_text(encoding="utf-8")
        assert "max_tokens=512" in text, f"{fname}：保留结论与实现不一致"
        seg_start = text.index("max_tokens=512") - 1200
        seg = text[max(seg_start, 0):text.index("max_tokens=512")]
        assert "甲 ⑤" in seg, f"{fname}：保留结论缺少就地依据说明"
        assert marker in seg, f"{fname}：保留结论缺少失败方向说明（{marker}）"


def test_u16c_c_group_sites_are_out_of_scope_and_unchanged():
    """C 组（非 P 阶段）明确不在"所有 P 环节"范围内，且本批次**未改动**它们。

    · `routes_models.py` 的 2048 —— 平台助手对话端点（source="platform_assistant"，D-073）。
      ⚠ 顺带更正：施工计划 §1.3 C 组把它写成"模型自测端点"，实测该行属 `assistant_chat`；
        自测/通用调用端点在同文件另一处（`max_tokens=req.max_tokens`，由请求体给值）。
    · `agent_loop.py` 的 2048 ×2 —— 平台助手工具循环，不是 P 阶段。
    · `fusion_execution_engine.py` 的 2048/4096 —— Fusion 是模型能力/策略，按 00-项目总览 §9.4
      不为其设额外流程特权。
    """
    routes = (_APP / "api" / "routes_models.py").read_text(encoding="utf-8")
    assert "max_tokens=2048" in routes and 'source="platform_assistant"' in routes
    agent_loop = (_SERVICES / "agent_loop.py").read_text(encoding="utf-8")
    assert agent_loop.count("max_tokens=2048") == 2
    fusion = (_SERVICES / "fusion_execution_engine.py").read_text(encoding="utf-8")
    assert "max_tokens=2048" in fusion and "max_tokens=4096" in fusion
