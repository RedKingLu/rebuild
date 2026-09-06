"""真跑校验器：以真实 token 消耗为判据，判定 Agent 是否真的在运行。

## 为什么需要这个（用户 2026-09-06 提出）

原话：「在测试成功和通过性的脚本上，有必要添加上实际 token 的消耗检测，这个是能够
真实反应 agent 是否在运行的标志，没有消耗或者少量消耗或者短时间的消耗等等，都是不
正常的」。

本项目此前**没有任何判据检查真实 token 消耗**（实测：`grep -rn "total_tokens" backend/tests/`
的全部命中都是 mock 桩值，如 `{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}`
与 `{"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}`）。这留下一类
**最难察觉的假通过**：阶段状态写成 `completed`、产物文件齐备，但模型根本没被真实调用
（或只被调了一次琐碎调用），产物来自模板/兜底/缓存。

这与项目既有教训同源 —— `R11-3`/`R14-5`「tests 绿 ≠ 真实库正常」、`D-097`「不得用
placeholder/mock 冒充 implemented」、`§10-20`「失败不得回退 mock」。**token 消耗是
"LLM 确实被调用过"的唯一客观物证**：它由 provider 侧返回、无法由平台自己伪造。

## 四类异常（对应用户列举的"没有消耗/少量消耗/短时间消耗"）

1. **零消耗**（`no_calls`）：该阶段无任何 call_log 行 ⇒ Agent 未真跑。
2. **消耗过少**（`too_few_tokens` / `too_few_calls`）：调用数或 token 总量低于该阶段
   的合理下限 ⇒ 可能只做了一次琐碎调用便宣告完成。
3. **耗时过短**（`too_fast`）：阶段首末调用时间跨度过短 ⇒ 真实 LLM 推理不可能这么快，
   典型是命中缓存或走了兜底分支。
4. **可疑的整齐值**（`suspicious_stub_values`）：token 值恰好是 mock 常用小整数
   （10/5/15、1/2/3 之类）⇒ 极可能是桩值被当成真实调用记入。

## 刻意不做的事（YAGNI + 不越界）

- **不设全局硬阈值**：不同阶段/项目规模的合理消耗差异极大（实测 R20-4：P0 约 2 万
  tokens、P4 约 165 万）。阈值必须由调用方按场景传入，本模块只提供判定机制。
- **不读 Key、不打印 Key**：只读 `call_log` 的统计列。
- **不替调用方决定"失败该怎么办"**：只返回结构化结论（含 `(code, message)` 二元组，
  沿用 R20-3 已吸收的 dsh 范式），由调用方决定是 assert 还是登记 evidence_gap。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

DEFAULT_DB = "/home/king/rebuild/backend/.data/rebuild.db"

# ── (code, message) 二元组的 code 常量（模块级字符串，刻意不用 Enum：
#    沿用 R20-3 ③§5.5 的论证——封闭 code 清单会撞 AGENTS §10-27）──
RC_NO_CALLS = "realrun-no-calls"
RC_TOO_FEW_CALLS = "realrun-too-few-calls"
RC_TOO_FEW_TOKENS = "realrun-too-few-tokens"
RC_TOO_FAST = "realrun-too-fast"
RC_STUB_VALUES = "realrun-suspicious-stub-values"
RC_NO_COMPLETION = "realrun-no-completion-tokens"
RC_ALL_FAILED = "realrun-all-calls-failed"
RC_SPAN_TOO_LONG = "realrun-span-too-long"   # ⚠ 告警级，不判 fail（见下）
RC_OK = "realrun-ok"

# mock 桩常用的 total_tokens 值（本仓实测出现过的）
_KNOWN_STUB_TOTALS = {2, 3, 5, 14, 15}

# ── span 异常长的默认告警阈值（用户 2026-09-06 提出：以最长 span 的两倍作阈值）──
#
# 基准取值说明（**刻意不用观测到的绝对最大值**）：
#   R20-4 两条真跑实测 span：信创 P4 = 3026s（合法最大）、现代化 P4 = 2204s、
#   信创 P3 = 1218s；另有现代化 P3 = 6660s —— **但那一条被主窗口误杀后端污染**
#   （停摆 1.85 小时，见 04-施工记录「R20-4 真跑一次中断的完整归因」），
#   它反映的是外部事故而非阶段真实工作量，**不可作为基准**。
#   ⇒ 取合法最大 3026s 的两倍 ≈ 6050s，向上取整到 6300s（1.75h）。
#
# 为什么是【告警】而不是【失败】（这条是设计要点）：
#   span 长有两种完全不同的成因 —— (a) 真的卡住/挂起（异常）；(b) 阶段本身工作量大
#   或 provider 限流导致重试退避（正常）。二者**无法仅凭时长区分**，判 fail 会产生
#   误报，而误报会训练人忽略告警。故本判据只产出 `warnings`，由人（或上层）介入判断。
#   这与本仓既有纪律一致：能力不确定时诚实标注而非武断裁决。
DEFAULT_SPAN_WARN_SECONDS = 6300.0


@dataclass
class StageUsage:
    """某阶段的真实调用统计（全部取自 call_log，provider 侧返回，平台无法伪造）。"""
    stage: str
    calls: int = 0
    completed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    first_at: Optional[str] = None
    last_at: Optional[str] = None
    span_seconds: float = 0.0
    providers: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    """判定结论。`passed=False` 时 `issues` 非空，每条为 (code, message) 二元组。

    `warnings` 与 `issues` 分离（用户 2026-09-06 要求的"告警后由人介入"）：
    warnings **不影响 `passed`**，只提示"这里可能不正常，请人看一眼"。
    """
    passed: bool
    usage: StageUsage
    issues: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"passed": self.passed, "usage": asdict(self.usage),
                "issues": self.issues, "warnings": self.warnings}

    def explain(self) -> str:
        u = self.usage
        head = (f"[{u.stage}] calls={u.calls}(completed={u.completed_calls}) "
                f"tokens p/c/t={u.prompt_tokens}/{u.completion_tokens}/{u.total_tokens} "
                f"span={u.span_seconds:.1f}s providers={u.providers}")
        lines = [head]
        if not self.passed:
            lines += [f"  ✗ [{i['code']}] {i['message']}" for i in self.issues]
        else:
            lines[0] = head + "  → 真跑判定通过"
        lines += [f"  ⚠ [{w['code']}] {w['message']}" for w in self.warnings]
        return "\n".join(lines)


def collect_stage_usage(project_id: str, stage: str, db_path: str = DEFAULT_DB) -> StageUsage:
    """从 call_log 汇总某项目某阶段的真实调用统计。

    call_log 由 ModelGateway 在真实调用后写入（含 provider 返回的 usage），
    **不是平台自己算的**，故可作为"LLM 确实被调用"的物证。
    """
    u = StageUsage(stage=stage)
    if not Path(db_path).exists():
        return u
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(call_log)")
        cols = {c[1] for c in cur.fetchall()}
        if not cols:
            return u
        # stage 列在部分历史行可能为空，故用 IS 判断而非等值
        cur.execute(
            "SELECT COUNT(*), "
            "       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END), "
            "       COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
            "       COALESCE(SUM(total_tokens),0), MIN(created_at), MAX(created_at) "
            "FROM call_log WHERE project_id=? AND stage=?",
            (project_id, stage))
        r = cur.fetchone() or ()
        if r and r[0]:
            (u.calls, u.completed_calls, u.prompt_tokens,
             u.completion_tokens, u.total_tokens, u.first_at, u.last_at) = (
                r[0], r[1] or 0, r[2], r[3], r[4], r[5], r[6])
        cur.execute("SELECT DISTINCT provider_id FROM call_log WHERE project_id=? AND stage=?",
                    (project_id, stage))
        u.providers = sorted({row[0] for row in cur.fetchall() if row[0]})
    finally:
        con.close()

    if u.first_at and u.last_at:
        from datetime import datetime
        try:
            fmt = "%Y-%m-%d %H:%M:%S.%f"
            u.span_seconds = (datetime.strptime(u.last_at, fmt)
                              - datetime.strptime(u.first_at, fmt)).total_seconds()
        except ValueError:
            u.span_seconds = 0.0
    return u


def assert_real_run(project_id: str, stage: str, *,
                    min_calls: int = 1,
                    min_total_tokens: int = 1000,
                    min_completion_tokens: int = 50,
                    min_span_seconds: float = 0.0,
                    span_warn_seconds: float = DEFAULT_SPAN_WARN_SECONDS,
                    db_path: str = DEFAULT_DB) -> Verdict:
    """判定某阶段是否为真跑。阈值**必须由调用方按场景传入**（见模块 docstring）。

    参数含义与选值提示（基于 R20-4 真跑实测，见 05-验收与证据索引.md）：
      min_calls            —— 该阶段最少真实调用次数。实测 P0=1~3、P1=6~12、P4=99~153
      min_total_tokens     —— 该阶段最少 token 总量。实测 P0≈2 万、P1≈7~18 万、P4≈165~269 万
      min_completion_tokens—— 最少**输出** token。只看 prompt 会被"发了大 prompt 但没生成"骗过
      min_span_seconds     —— 首末调用最小时间跨度。0 = 不检查（单次调用阶段应设 0）
      span_warn_seconds    —— **告警**阈值（非失败）：跨度超此值提示人工介入判断是否卡住。
                              默认 DEFAULT_SPAN_WARN_SECONDS，设 0 关闭该告警。
    """
    u = collect_stage_usage(project_id, stage, db_path)
    issues: list[dict] = []
    warnings: list[dict] = []

    if u.calls == 0:
        issues.append({"code": RC_NO_CALLS,
                       "message": f"阶段 {stage} 无任何模型调用记录（call_log 零行）"
                                  f" —— Agent 未真实调用 LLM，产物若存在则来源可疑"})
        return Verdict(passed=False, usage=u, issues=issues)

    if u.completed_calls == 0:
        issues.append({"code": RC_ALL_FAILED,
                       "message": f"阶段 {stage} 有 {u.calls} 次调用但**无一 completed**"
                                  f" —— 全部失败，不构成真跑证据"})

    if u.calls < min_calls:
        issues.append({"code": RC_TOO_FEW_CALLS,
                       "message": f"调用次数 {u.calls} < 下限 {min_calls}"
                                  f" —— 可能只做了琐碎调用便宣告完成"})

    if u.total_tokens < min_total_tokens:
        issues.append({"code": RC_TOO_FEW_TOKENS,
                       "message": f"token 总量 {u.total_tokens} < 下限 {min_total_tokens}"
                                  f" —— 消耗过少，与该阶段应有的工作量不符"})

    if u.completion_tokens < min_completion_tokens:
        issues.append({"code": RC_NO_COMPLETION,
                       "message": f"输出 token {u.completion_tokens} < 下限 {min_completion_tokens}"
                                  f" —— 模型几乎没有生成内容（只看 prompt 会被『发了大提示词但没产出』骗过）"})

    if min_span_seconds > 0 and u.span_seconds < min_span_seconds:
        issues.append({"code": RC_TOO_FAST,
                       "message": f"首末调用跨度 {u.span_seconds:.1f}s < 下限 {min_span_seconds}s"
                                  f" —— 真实 LLM 推理不可能这么快，典型是命中缓存或走了兜底分支"})

    if u.total_tokens in _KNOWN_STUB_TOTALS:
        issues.append({"code": RC_STUB_VALUES,
                       "message": f"token 总量恰为 {u.total_tokens}，与本仓 mock 桩常用值"
                                  f"（{sorted(_KNOWN_STUB_TOTALS)}）重合 —— 疑为桩值被记为真实调用"})

    # ── 告警（不影响 passed）：span 异常长，可能卡住，须人工介入判断 ──
    if span_warn_seconds > 0 and u.span_seconds > span_warn_seconds:
        warnings.append({
            "code": RC_SPAN_TOO_LONG,
            "message": (f"首末调用跨度 {u.span_seconds:.0f}s 超过告警阈值 {span_warn_seconds:.0f}s"
                        f"（≈{u.span_seconds/3600:.1f} 小时）—— **须人工介入判断**："
                        f"是真的卡住/挂起，还是该阶段工作量大或 provider 限流退避所致。"
                        f"排查建议：① 查 call_log 相邻调用的时间间隔，找出最大空档落在哪一次；"
                        f"② 查后端日志有无 rate_limited / StreamStallTimeout；"
                        f"③ 确认驱动方（外部脚本/前端）进程是否仍存活。"
                        f"本判据刻意只告警不判 fail —— 时长无法区分上述两种成因，"
                        f"误报会训练人忽略告警。")})

    return Verdict(passed=not issues, usage=u, issues=issues, warnings=warnings)


def assert_real_run_multi(project_id: str, stage_thresholds: dict[str, dict],
                          db_path: str = DEFAULT_DB) -> dict[str, Verdict]:
    """多阶段批量判定。`stage_thresholds` 形如 {"p1": {"min_calls": 5, ...}, ...}。"""
    return {stage: assert_real_run(project_id, stage, db_path=db_path, **kw)
            for stage, kw in stage_thresholds.items()}
