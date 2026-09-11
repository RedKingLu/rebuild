"""D-06 返工：`acceptance_baseline_service` 的 LLM JSON 截断可诊断 + 输出预算 env 可调。

事实源：`产物/草稿/V26.2-总验收真跑-问题梳理与返工修复计划.md` §3.3 D-06 / §4 批次 A。
真实规模真跑（1018 文件）实测：本服务的调用 completion **触顶原硬编码 max_tokens=6144**
（call_log `call_5aa2351f4c19` completion_tokens=6144 = 上限），静态基线 JSON 中途截断 →
`_parse` 报 `Unterminated string starting at: line 131 column 15 (char 7513)` → 静态基线空壳。
与 R11-7（B-P3-NO-TASKPLANS）同族根因，本轮复用其 env 可调范式（不新造机制）。

本测试锁定四件事，且**不放宽解析严格性**（截断响应仍必须解析失败）：
  ① 截断响应 → 解析失败，但日志含可诊断信息（响应字符长度 + 疑似截断标志）；
  ② 合法完整 JSON → 正常解析，行为不回归；
  ③ 真正非法 JSON（结构收敛但键名/语法错）→ 与截断情形**可区分**（日志级别与信号不同）；
  ④ env 可调：`P1_BASELINE_MAX_TOKENS` / `P1_BASELINE_TIMEOUT` 生效并真正透传到 gateway.call。
另锁定：解析失败**不得伪造成功**——不得凭空产出断言/规格冒充完成。

不发起真实 LLM 调用（成本）：全部用 fake gateway 构造响应，沿用既有
`tests/test_wp2_p1_agent_workflow.py` 的可测性范式（生产路径无 mock）。
"""

from __future__ import annotations

import importlib
import json
import logging
from types import SimpleNamespace

import pytest

from app.services.acceptance_baseline_service import AcceptanceBaselineService

_LOGGER_NAME = "rebuild.acceptance_baseline_service"

# 完整合法的静态基线响应（对照组）
_COMPLETE = json.dumps({
    "test_assertions": [{"path": "tests/test_a.py", "what_it_verifies": "登录返回 200"}],
    "missing_test_paths": [{"path_or_module": "App_Code/Order.cs", "why_critical": "核心下单无测试"}],
    "characterization_specs": [{"target": "Order.Create", "input": "1 件商品",
                                "expected_output_anchor": "订单号非空", "rationale": "锁定原始行为"}],
    "dynamic_golden_plan": {"runnable_on_platform": False, "language": "csharp",
                            "run_command": "", "working_subdir": "",
                            "reason": ".NET Framework 需 Windows",
                            "needs_env": {"kind": "windows", "note": "经 R14 远程"}},
}, ensure_ascii=False)

# 截断响应：把完整响应在一个中文字符串值中途砍断（复刻真跑现象——末尾停在未闭合字符串内）
_TRUNCATED = _COMPLETE[:_COMPLETE.index("核心下单无测试") + 4]

# 真正非法的 JSON：结构收敛（括号配平、字符串闭合、以 } 结尾），但键名没加引号
_MALFORMED = '{test_assertions: [], missing_test_paths: []}'


class _FakeGateway:
    """Key 存在的 fake gateway：返回预设响应，并记录收到的调用参数（断言透传）。"""

    def __init__(self, content: str):
        self._content = content
        self.calls: list[dict] = []

    def get_status(self):
        return SimpleNamespace(overall_status="available")

    def stage_model_readiness(self, **kwargs):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        self.calls.append(kw)
        return {"status": "completed", "model": "fake-model", "content": self._content}


def _service(content: str) -> tuple[AcceptanceBaselineService, _FakeGateway]:
    gw = _FakeGateway(content)
    return AcceptanceBaselineService(gateway=gw), gw


async def _capture(svc: AcceptanceBaselineService):
    """静态部分即被测面；run_dynamic=False 以免触碰 ExecutionProvider（本测试不测动态捕获）。"""
    return await svc.capture("d06-proj", facts={"test_candidates": []}, upstream={},
                             run_id="run-d06", run_dynamic=False)


# ── ① 截断响应 → 解析失败 + 日志可诊断 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_truncated_response_fails_parse_and_logs_diagnosable_info(caplog):
    svc, _ = _service(_TRUNCATED)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await _capture(svc)

    # 解析严格性不放宽：截断响应必须仍然解析失败（不做 JSON 修补、不猜补尾巴）
    assert result.baseline.get("static_parse_error") is True

    records = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert records, "解析失败必须发声（公理3 禁静默）"
    rec = records[-1]
    msg = rec.getMessage()
    # 截断的可操作结论是"加输出预算"，故用 error 级别与 warning（非法 JSON）区分
    assert rec.levelno == logging.ERROR, f"截断应报 ERROR，实际 {rec.levelname}: {msg}"
    assert "疑似被截断" in msg
    # 必含：原始响应字符长度
    assert f"响应长度={len(_TRUNCATED)}" in msg
    # 必含：疑似截断的具体信号（末尾未闭合）
    assert "unclosed_string" in msg
    # 必含：模型与预算取值（供判断是否该调预算）
    assert "fake-model" in msg
    assert "max_tokens=" in msg and "timeout=" in msg
    assert "P1_BASELINE_MAX_TOKENS" in msg

    # 诊断信息同样落进产物（日志会滚走，读产物的人也要能分辨失败模式）
    diag = result.baseline["static_parse_diagnosis"]
    assert diag["suspected_truncation"] is True
    assert diag["content_len"] == len(_TRUNCATED)


@pytest.mark.asyncio
async def test_parse_failure_does_not_fabricate_success_content(caplog):
    """解析失败不得伪造：不得凭空产出断言/缺测路径/特征化规格冒充完成（D-097/公理3）。"""
    svc, _ = _service(_TRUNCATED)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await _capture(svc)

    static = result.baseline["static_baseline"]
    assert static["test_assertions"] == []
    assert static["missing_test_paths"] == []
    assert static["characterization_specs"] == []
    # 失败标记必须存在且可被下游消费，不允许只留一个"看起来完成"的产物
    assert result.baseline["static_parse_error"] is True
    assert result.baseline["static_parse_diagnosis"]["truncation_signals"]


# ── ② 合法完整 JSON → 不回归 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_json_parses_normally_no_regression(caplog):
    svc, _ = _service(_COMPLETE)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await _capture(svc)

    assert result.status == "completed"
    assert "static_parse_error" not in result.baseline
    assert "static_parse_diagnosis" not in result.baseline
    static = result.baseline["static_baseline"]
    assert len(static["test_assertions"]) == 1
    assert len(static["missing_test_paths"]) == 1
    assert len(static["characterization_specs"]) == 1
    assert result.model_used == "fake-model"
    # 成功路径不得产生解析告警噪音
    assert [r for r in caplog.records if r.name == _LOGGER_NAME] == []


# ── ③ 真正非法 JSON（非截断）→ 与截断可区分 ────────────────────────────────

@pytest.mark.asyncio
async def test_malformed_but_complete_json_is_distinguishable_from_truncation(caplog):
    svc, _ = _service(_MALFORMED)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await _capture(svc)

    assert result.baseline.get("static_parse_error") is True
    rec = [r for r in caplog.records if r.name == _LOGGER_NAME][-1]
    msg = rec.getMessage()
    # 非截断：报 WARNING 且措辞明确指向 prompt 契约，不是加预算
    assert rec.levelno == logging.WARNING, f"非截断应报 WARNING，实际 {rec.levelname}: {msg}"
    assert "非截断" in msg and "prompt 契约" in msg
    assert "疑似被截断" not in msg
    assert f"响应长度={len(_MALFORMED)}" in msg

    diag = result.baseline["static_parse_diagnosis"]
    assert diag["suspected_truncation"] is False, "结构已收敛的非法 JSON 不得被误判为截断"
    assert diag["truncation_signals"] == []


def test_shape_scanner_separates_truncation_from_malformed():
    """结构扫描器本身的判据：截断=未闭合；非法但完整=已收敛。"""
    svc, _ = _service("")
    assert svc._scan_json_shape(_TRUNCATED) == (3, True)      # 3 层未闭合 + 停在字符串内
    assert svc._scan_json_shape(_MALFORMED) == (0, False)     # 结构收敛
    assert svc._scan_json_shape(_COMPLETE) == (0, False)


# ── ④ env 可调（复用 R11-7 范式）→ 取值生效且真正透传 ──────────────────────

def test_budget_defaults_and_env_override(monkeypatch):
    """默认 16384 / 240s；env 覆盖后重载模块，取值生效（不必真调模型）。"""
    import app.services.acceptance_baseline_service as mod

    reloaded = importlib.reload(mod)
    assert reloaded._BASELINE_MAX_TOKENS == 16384, "默认输出预算须显著高于触顶值 6144"
    assert reloaded._BASELINE_TIMEOUT == 240.0, "默认超时须高于 adapter fail-fast 的 60s"

    monkeypatch.setenv("P1_BASELINE_MAX_TOKENS", "20480")
    monkeypatch.setenv("P1_BASELINE_TIMEOUT", "300")
    reloaded = importlib.reload(mod)
    try:
        assert reloaded._BASELINE_MAX_TOKENS == 20480
        assert reloaded._BASELINE_TIMEOUT == 300.0
    finally:
        monkeypatch.undo()
        importlib.reload(mod)  # 复原模块级常量，避免污染同进程其它测试


@pytest.mark.asyncio
async def test_budget_is_passed_through_to_gateway_call():
    """预算与超时须真的透传给 ModelGateway.call（不硬编码模型/endpoint，D-098）。"""
    import app.services.acceptance_baseline_service as mod

    svc, gw = _service(_COMPLETE)
    await _capture(svc)

    assert len(gw.calls) == 1
    kw = gw.calls[0]
    assert kw["max_tokens"] == mod._BASELINE_MAX_TOKENS
    assert kw["timeout"] == mod._BASELINE_TIMEOUT
    # 走策略解析，调用方不得指定具体模型/endpoint
    assert kw["strategy_id"] == "system-default"
    assert "model" not in kw and "endpoint" not in kw and "api_base" not in kw
