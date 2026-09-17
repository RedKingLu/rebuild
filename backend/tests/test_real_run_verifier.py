"""真跑校验器测试（R20-4 起，用户 2026-09-06 提出的 token 消耗判据）。

断言一律打在**真实 call_log 数据**与**真实判定行为**上，不断言"函数可导入"
（AGENTS §10-17 在测试层的表现）。

阈值参考值来自 R20-4 两条真跑的实测（见 `证据/进度追踪/05-验收与证据索引.md`）：

| 阶段 | 现代化跑（7 节点） | 信创跑（12 节点） | 说明 |
|---|---|---|---|
| p0 | 3 次 / 5.8 万 tok | 1 次 / 2.1 万 tok | 接入阶段，调用少 |
| p1 | 12 次 / 18.2 万 | 6 次 / 7.2 万 | 14 分析维度 |
| p2 | 4 次 / 9.6 万 | 5 次 / 13.7 万 | 评估 |
| p3 | 25 次 / 27.8 万 | 37 次 / 49.7 万 | 规划，节点数越多调用越多 |
| p4 | 99 次 / 165 万 | 153 次 / 269 万 | **消耗大头**，逐节点生成代码 |
| p5 | 8 次 / 16.3 万 | — | 验证 |

⇒ **阈值必须按阶段与项目规模设定，不存在全局通用值**（这正是 verifier 不内置
硬阈值、要求调用方传入的原因）。
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.services.real_run_verifier import (
    RC_ALL_FAILED,
    RC_NO_CALLS,
    RC_NO_COMPLETION,
    RC_STUB_VALUES,
    RC_TOO_FAST,
    RC_TOO_FEW_CALLS,
    RC_TOO_FEW_TOKENS,
    assert_real_run,
    collect_stage_usage,
)


def _mk_db(rows: list[dict]) -> str:
    """建一个只含 call_log 的临时库，rows 为待插入的调用记录。"""
    fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    fd.close()
    con = sqlite3.connect(fd.name)
    con.execute("""CREATE TABLE call_log (
        model_call_id TEXT, project_id TEXT, stage TEXT, provider_id TEXT,
        status TEXT, prompt_tokens INT, completion_tokens INT, total_tokens INT,
        created_at TEXT)""")
    for i, r in enumerate(rows):
        con.execute(
            "INSERT INTO call_log (model_call_id,project_id,stage,provider_id,status,"
            "prompt_tokens,completion_tokens,total_tokens,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"mc-{i}", r.get("project_id", "P"), r.get("stage", "p1"),
             r.get("provider_id", "prov"), r.get("status", "completed"),
             r.get("pt", 1000), r.get("ct", 200), r.get("tt", 1200),
             r.get("at", f"2026-09-06 10:00:{i:02d}.000000")))
    con.commit()
    con.close()
    return fd.name


# ── 零消耗：最重要的一条 —— 产物齐备但模型没被调用 ──────────────────────

def test_zero_calls_is_not_a_real_run():
    """无任何 call_log 行 ⇒ 判 fail，code=realrun-no-calls。

    这是本校验器存在的首要理由：阶段可能写着 completed、产物文件齐备，
    但 LLM 从未被调用（产物来自模板/兜底/缓存）。
    """
    db = _mk_db([])
    v = assert_real_run("P", "p1", db_path=db)
    assert v.passed is False
    assert [i["code"] for i in v.issues] == [RC_NO_CALLS]
    assert v.usage.calls == 0 and v.usage.total_tokens == 0
    Path(db).unlink()


def test_all_calls_failed_is_not_a_real_run():
    """有调用但无一 completed ⇒ 不构成真跑证据。"""
    db = _mk_db([{"status": "failed"}, {"status": "failed"}])
    v = assert_real_run("P", "p1", db_path=db, min_total_tokens=1, min_completion_tokens=1)
    assert v.passed is False
    assert RC_ALL_FAILED in [i["code"] for i in v.issues]
    Path(db).unlink()


# ── 消耗过少 ─────────────────────────────────────────────────────────

def test_too_few_tokens_is_flagged():
    """token 总量低于下限 ⇒ 消耗与该阶段应有工作量不符。"""
    db = _mk_db([{"pt": 100, "ct": 20, "tt": 120}])
    v = assert_real_run("P", "p1", db_path=db, min_total_tokens=50_000,
                        min_completion_tokens=1, min_calls=1)
    assert v.passed is False
    assert RC_TOO_FEW_TOKENS in [i["code"] for i in v.issues]
    Path(db).unlink()


def test_too_few_calls_is_flagged():
    db = _mk_db([{"stage": "p4", "pt": 999_999, "ct": 999_999, "tt": 1_999_998}])
    v = assert_real_run("P", "p4", db_path=db, min_calls=50, min_total_tokens=1,
                        min_completion_tokens=1)
    assert v.passed is False
    assert RC_TOO_FEW_CALLS in [i["code"] for i in v.issues]
    Path(db).unlink()


def test_prompt_only_consumption_is_flagged():
    """只有 prompt 没有 completion ⇒ 模型没生成内容。

    这条防的是"发了大提示词但模型没产出"——只看 total_tokens 会被骗过，
    因为 prompt 本身就能把 total 撑得很大。
    """
    db = _mk_db([{"stage": "p4", "pt": 500_000, "ct": 0, "tt": 500_000}])
    v = assert_real_run("P", "p4", db_path=db, min_total_tokens=1000,
                        min_completion_tokens=1000, min_calls=1)
    assert v.passed is False
    assert RC_NO_COMPLETION in [i["code"] for i in v.issues]
    Path(db).unlink()


# ── 耗时过短 ─────────────────────────────────────────────────────────

def test_too_fast_span_is_flagged():
    """首末调用跨度过短 ⇒ 真实 LLM 推理不可能这么快（典型：命中缓存/走兜底）。"""
    rows = [{"at": "2026-09-06 10:00:00.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000},
            {"at": "2026-09-06 10:00:01.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000}]
    db = _mk_db(rows)
    v = assert_real_run("P", "p1", db_path=db, min_calls=1, min_total_tokens=1000,
                        min_completion_tokens=100, min_span_seconds=60)
    assert v.passed is False
    assert RC_TOO_FAST in [i["code"] for i in v.issues]
    assert v.usage.span_seconds == pytest.approx(1.0, abs=0.01)
    Path(db).unlink()


def test_span_check_is_opt_in():
    """min_span_seconds=0（默认）时不检查跨度 —— 单次调用阶段不应被误判。"""
    db = _mk_db([{"stage": "p0", "pt": 50_000, "ct": 5_000, "tt": 55_000}])
    v = assert_real_run("P", "p0", db_path=db, min_calls=1, min_total_tokens=1000,
                        min_completion_tokens=100)
    assert v.passed is True, v.explain()
    assert v.usage.span_seconds == 0.0
    Path(db).unlink()


# ── 桩值识别 ─────────────────────────────────────────────────────────

def test_known_mock_stub_totals_are_flagged():
    """token 总量恰为本仓 mock 桩常用值 ⇒ 疑为桩值被记为真实调用。

    实测本仓既有测试用过 total_tokens=15（`{"prompt_tokens":10,"completion_tokens":5}`）
    与 total_tokens=3（`{"prompt_tokens":1,"completion_tokens":2}`）。
    """
    for stub_total in (3, 15):
        db = _mk_db([{"pt": 1, "ct": 2, "tt": stub_total}])
        v = assert_real_run("P", "p1", db_path=db, min_calls=1,
                            min_total_tokens=1, min_completion_tokens=1)
        assert v.passed is False, f"total={stub_total} 应被标记"
        assert RC_STUB_VALUES in [i["code"] for i in v.issues]
        Path(db).unlink()


# ── 正向：真跑量级的数据应通过 ─────────────────────────────────────────

def test_realistic_p4_usage_passes():
    """按 R20-4 实测的 P4 量级（99 次 / 165 万 tok）构造 ⇒ 应判通过。"""
    rows = [{"stage": "p4", "pt": 15_000, "ct": 1_000, "tt": 16_000,
             "at": f"2026-09-06 10:{i//60:02d}:{i%60:02d}.000000"} for i in range(99)]
    db = _mk_db(rows)
    v = assert_real_run("P", "p4", db_path=db, min_calls=50,
                        min_total_tokens=1_000_000, min_completion_tokens=50_000,
                        min_span_seconds=60)
    assert v.passed is True, v.explain()
    assert v.usage.calls == 99
    assert v.usage.total_tokens == 99 * 16_000
    Path(db).unlink()


def test_usage_aggregation_is_per_stage():
    """统计须按 stage 隔离 —— 不得把别的阶段的消耗算进来。"""
    db = _mk_db([{"stage": "p1", "tt": 10_000, "pt": 9_000, "ct": 1_000},
                 {"stage": "p4", "tt": 90_000, "pt": 80_000, "ct": 10_000}])
    assert collect_stage_usage("P", "p1", db).total_tokens == 10_000
    assert collect_stage_usage("P", "p4", db).total_tokens == 90_000
    Path(db).unlink()


def test_providers_are_recorded_for_attribution():
    """须记录 provider —— 用户 2026-09-06 因看错 provider 控制台而怀疑没在跑，
    provider 归属是排查此类疑问的第一手信息。"""
    db = _mk_db([{"provider_id": "maas-icompify"}, {"provider_id": "maas-icompify"}])
    u = collect_stage_usage("P", "p1", db)
    assert u.providers == ["maas-icompify"]
    Path(db).unlink()


def test_verdict_issues_carry_code_and_message():
    """每条 issue 须为 (code, message) 二元组（沿用 R20-3 吸收的 dsh 范式）。"""
    db = _mk_db([])
    v = assert_real_run("P", "p1", db_path=db)
    assert v.issues
    for i in v.issues:
        assert set(i.keys()) == {"code", "message"}
        assert i["code"].startswith("realrun-")
        assert i["message"] and len(i["message"]) > 10
    assert "passed" in v.as_dict() and "usage" in v.as_dict()
    Path(db).unlink()


# ── span 异常长的告警（用户 2026-09-06 提出：先告警、由人介入判断）──────────

def test_span_too_long_warns_but_does_not_fail():
    """span 超阈值 ⇒ 产出 warning 且 **passed 仍为 True**。

    设计要点：span 长有两种成因 —— (a) 真卡住（异常）(b) 工作量大或 provider 限流退避
    （正常）。二者**无法仅凭时长区分**，判 fail 会误报，而误报会训练人忽略告警。
    故只告警、由人介入。
    """
    from app.services.real_run_verifier import RC_SPAN_TOO_LONG
    rows = [{"stage": "p3", "at": "2026-09-06 10:00:00.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000},
            {"stage": "p3", "at": "2026-09-06 12:00:00.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000}]
    db = _mk_db(rows)
    v = assert_real_run("P", "p3", db_path=db, min_calls=1, min_total_tokens=1000,
                        min_completion_tokens=100, span_warn_seconds=6300)
    assert v.passed is True, "span 告警不得判 fail"
    assert not v.issues
    assert [w["code"] for w in v.warnings] == [RC_SPAN_TOO_LONG]
    assert "须人工介入判断" in v.warnings[0]["message"]
    assert v.usage.span_seconds == pytest.approx(7200.0, abs=1)
    Path(db).unlink()


def test_span_warning_is_silent_under_threshold():
    """未超阈值 ⇒ 无告警（防误报：正常长阶段不该刷告警）。"""
    rows = [{"stage": "p4", "at": "2026-09-06 10:00:00.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000},
            {"stage": "p4", "at": "2026-09-06 10:50:00.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000}]
    db = _mk_db(rows)
    v = assert_real_run("P", "p4", db_path=db, min_calls=1, min_total_tokens=1000,
                        min_completion_tokens=100, span_warn_seconds=6300)
    assert v.passed is True and not v.warnings
    Path(db).unlink()


def test_span_warning_can_be_disabled():
    """span_warn_seconds=0 ⇒ 关闭告警（某些批量场景不需要）。"""
    rows = [{"stage": "p3", "at": "2026-09-06 10:00:00.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000},
            {"stage": "p3", "at": "2026-09-06 20:00:00.000000", "pt": 50_000, "ct": 5_000, "tt": 55_000}]
    db = _mk_db(rows)
    v = assert_real_run("P", "p3", db_path=db, min_calls=1, min_total_tokens=1000,
                        min_completion_tokens=100, span_warn_seconds=0)
    assert v.passed is True and not v.warnings
    Path(db).unlink()


def test_warnings_and_issues_are_separate_channels():
    """warnings 与 issues 是两个通道：可同时存在，且只有 issues 影响 passed。"""
    rows = [{"stage": "p3", "at": "2026-09-06 10:00:00.000000", "pt": 100, "ct": 10, "tt": 110},
            {"stage": "p3", "at": "2026-09-06 13:00:00.000000", "pt": 100, "ct": 10, "tt": 110}]
    db = _mk_db(rows)
    v = assert_real_run("P", "p3", db_path=db, min_calls=1, min_total_tokens=500_000,
                        min_completion_tokens=1, span_warn_seconds=6300)
    assert v.passed is False          # issues 有 → fail
    assert v.issues and v.warnings    # 两个通道同时非空
    d = v.as_dict()
    assert "warnings" in d and "issues" in d
    Path(db).unlink()
