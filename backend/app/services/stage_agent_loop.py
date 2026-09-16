"""Shared P0-P3 stage tool-calling loop (R17.5-P4-FIX 批2, D-110).

The P0-P3 identification/planning stages are target-driven Node Worker Agent loops
(AGENTS §2.3). Historically each service made a SINGLE `gateway.call(...)` against a
pre-assembled deterministic fact pack — the agent could not read the real source on
demand or reason across multiple rounds. This helper turns that single shot into the
SAME bounded tool-calling loop P4 already uses (p4_execution_worker._generate_with_tools),
so the agent can call list_files / code_grep / fs_read / read_artifact / get_project_info
against the real materialized source (给路径+按需读取, 用户 2026-07-23) and still return
the stage's structured contract as its final answer.

Design invariants:
  - The deterministic fact pack stays as HELP in the user prompt — it is NOT a read cap
    (D-110): the agent may read more of the real source on demand, multi-round.
  - The stage's structured output CONTRACT is unchanged (D-108, skill-first): the caller
    still parses the final text into its anchor fields. This helper only changes HOW the
    text is produced (multi-round tool loop vs single shot), never the contract.
  - No stage tool whitelist (D-110 / 禁止项26): the FULL enabled toolset is loaded
    (load_schemas is not stage-filtered); tool safety is owned entirely by the L0-L5
    grading inside execute_tool (L3+ → action_approval Gate). Identification stages
    naturally use the L0/L1 read tools; write tools gate.
  - Streaming call_stream naturally reads the project's model selection
    (_project_preferred_ref → global_model_ref), so P0-P3 honour the user's model choice.
  - No model / all-failed → honest failed result with attempted_chain (D-097/公理3):
    never a rule-based fallback, never a fabricated completion.
  - Gateways without call_stream (injected test doubles) transparently fall back to a
    single gateway.call — preserving the existing blocked/parse test paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("rebuild.stage_agent_loop")

# ── V26.2 返工批次二（Q-B2-1 / Q-B2-5）：P 环节的输出预算旋钮与截断诊断，单一实现 ──────
# 用户 2026-09-16 裁决：**所有 P 环节暂时不设硬 token 预算**。前史（不要删——它解释了
# 为什么"把数字调高"这条路已被否决）：
#   · 批次 B/F 把 P1 profiling=32768 / P1 baseline=16384 / P3 planning=32768 /
#     P4 gen=32768 / P5 verification=16384 改成 env 可调并抬高默认值；
#   · 但 P0 intake / P1 tech_selection / P2 assessment（均 16384 硬编码）与 P6 delivery
#     （8192 硬编码）未改，真实规模（MicroOA 1018 文件）真跑时 P0 撞 16383、
#     P2 撞 16384 / 16387 —— 即"抬高天花板"只是把天花板挪一格，样本再大一点就再撞一次。
# ⇒ 本批次取消平台侧硬编码天花板本身，把输出上限交还给「模型自身最大输出长度」+
#    「adapter 的三层时间护栏」（_STREAM_TOTAL_TIMEOUT / _REQUEST_TIMEOUT / 首字节超时，
#    本批次**不得**放宽）。**注意：这不等于"截断风险已消除"** —— 平台侧天花板移除后，
#    模型自身上限与时间护栏仍可能造成输出不完整，故截断诊断比以前更重要（见下）。
# 旋钮保留、默认"不设"：成本失控时运维仍可设一个上限救急（逃生阀），且不设时请求体里
# 根本不出现 max_tokens 键（见 litellm_adapter 的条件写入）。


def optional_int_env(name: str) -> Optional[int]:
    """读一个"不设即无上限"的整数型 env 旋钮：未设置 / 空串 → None（不设预算）。

    取值非法（非整数）时**不静默吞掉**（公理3/AGENTS §10-21）：告警并按"不设"处理 ——
    宁可无平台天花板（有时间护栏兜底），也不要因一个配置笔误把预算悄悄设成 0 / 崩在
    模块导入期。返回 <=0 同样视为"不设"（0 或负数作为上限无意义）。
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        logger.warning("env 旋钮 %s 取值 %r 不是整数，已按「不设上限」处理", name, raw)
        return None
    if val <= 0:
        logger.warning("env 旋钮 %s 取值 %d <= 0，已按「不设上限」处理", name, val)
        return None
    return val


# 诊断里 max_tokens 字段在"不设预算"时的显示口径：**不写 null**，写清"平台没设、
# 由谁决定"，使读产物/日志的人一眼看得出"这次截断不是平台砍的"（U8）。
MAX_TOKENS_UNSET_DISPLAY = "未设置（无平台侧上限，由模型自身最大输出长度与超时护栏决定）"


def scan_json_shape(text: str) -> tuple[int, bool]:
    """扫描 JSON 文本的结构收敛状态：返回 (未闭合的括号深度, 是否停在字符串内部)。

    纯诊断用，**不做任何 JSON 修补、不放宽解析严格性** —— 只用来区分「输出被截断（结构
    未闭合）」与「输出完整但非法（键名/引号写错）」这两种处置完全不同的失败。

    Q-B2-2（用户 2026-09-16 裁决）：本函数与 diagnose_parse_failure 是全平台**唯一**实现，
    原先散在 profiling_service / acceptance_baseline_service / p5_verification_agent 的三份
    逐字副本已改为调用本函数（防三份副本各自漂移）。
    """
    depth = 0
    in_string = False
    escaped = False
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


def diagnose_parse_failure(text: str, *, max_tokens: Optional[int] = None,
                           timeout_s: Optional[float] = None, env_knobs: str = "",
                           decode_error: Optional[Exception] = None) -> dict:
    """构造 JSON 解析失败的可诊断信息（D-06 同族，共享实现）。

    让"解析失败"不再只是一句 parse_error —— 读产物/日志的人能分辨到底是「被截断」还是
    「输出了非法 JSON」，两者的可操作结论完全不同（前者查上限/护栏，后者改 prompt 契约）。

    `max_tokens=None` 表示本次调用**未设平台侧上限**，此时 `max_tokens` 字段填
    MAX_TOKENS_UNSET_DISPLAY 而非 null —— 取消硬预算后这一区分是现场归因的关键：
    截断信号 + "未设置" ⇒ 原因在模型自身上限 / provider 侧 / 时间护栏，不在平台预算。

    `decode_error` 仅在调用方确有异常对象时写入（保持 acceptance_baseline_service 既有
    诊断体形状不变）。
    """
    depth, in_string = scan_json_shape(text)
    signals: list[str] = []
    if not text:
        # 空响应也是预算/护栏问题的已知形态（R17.5-P4-FIX 批2.8 实测：推理链耗尽输出预算后
        # 最终文本为空）；归入"疑似截断"以给出同一个可操作结论，措辞保留不确定性。
        signals.append("empty_content: 响应为空（可能推理链耗尽输出预算后无最终文本）")
    if in_string:
        signals.append("unclosed_string: 文本末尾停在未闭合的字符串内")
    if depth > 0:
        signals.append(f"unbalanced_depth={depth}: 有 {depth} 层 {{/[ 未闭合")
    if text and not text.rstrip().endswith(("}", "]")):
        signals.append("tail_not_closed: 末尾字符不是 } 或 ]")
    diagnosis: dict = {
        "content_len": len(text),
        "suspected_truncation": bool(signals),
        "truncation_signals": signals,
    }
    if decode_error is not None:
        diagnosis["decode_error"] = f"{type(decode_error).__name__}: {decode_error}"
    diagnosis["tail_snippet"] = text[-80:] if text else ""
    diagnosis["max_tokens"] = max_tokens if max_tokens is not None else MAX_TOKENS_UNSET_DISPLAY
    diagnosis["timeout_s"] = timeout_s
    diagnosis["env_knobs"] = env_knobs
    return diagnosis


def log_parse_failure(log, label: str, diagnosis: dict, *,
                      model_used: Optional[str] = None) -> None:
    """按失败模式分级发声（公理3，共享实现）：疑似截断 → error 级并给出旋钮与上限口径；
    结构已收敛但非法 JSON → warning 级并指向 prompt 契约。`label` 为阶段标识（如
    "P0 intake"），`log` 为调用方自己的 logger（保持日志归属不变）。"""
    if diagnosis.get("suspected_truncation"):
        log.error(
            "%s: LLM 输出**疑似被截断**导致 JSON 解析失败（D-06 同族）—— 响应长度=%d 字符, "
            "截断信号=%s, 尾部=%r, model=%s, 本次上限 max_tokens=%s / timeout=%ss"
            "（可经 %s 设置一个显式上限）",
            label, diagnosis.get("content_len", 0), diagnosis.get("truncation_signals"),
            diagnosis.get("tail_snippet"), model_used, diagnosis.get("max_tokens"),
            diagnosis.get("timeout_s"), diagnosis.get("env_knobs") or "（本调用无 env 旋钮）")
    else:
        log.warning(
            "%s: LLM 输出**结构已收敛但非法 JSON**（非截断，需修 prompt 契约）—— "
            "响应长度=%d 字符, 尾部=%r, model=%s",
            label, diagnosis.get("content_len", 0), diagnosis.get("tail_snippet"), model_used)

_MAX_TOOL_ROUNDS = 6  # bounded tool-calling rounds (mirror p4 worker / agent_loop)

# 本循环单次模型调用的默认请求超时（秒）。原为 `run_stage_tool_loop` 签名里的字面量 180.0；
# V26.2 返工批次二把它提成命名常量，唯一目的是让**未显式传 timeout 的调用点**能在截断诊断里
# 如实写出"本次真实生效的超时是多少"，而不是写 null 或另抄一个 180（取值不变，非行为变更）。
DEFAULT_STAGE_TIMEOUT_SECONDS = 180.0

# R21 dsh 范式吸收「循环卫生」①：同一 (工具名+参数) 组合连续调用达到此轮数时，
# 提醒模型换方法或收尾 —— 只提醒，不拦截（本次调用仍照常执行）。阈值与文案均为
# 平台通用能力，不含任何项目/场景特定内容。
_REPEAT_TOOL_CALL_THRESHOLD = 3
_REPEAT_TOOL_CALL_REMINDER_TEMPLATE = (
    "系统提醒：你已连续 {n} 次调用完全相同的工具（工具名与参数完全一致）。"
    "如果这个方法没有取得新的进展，请改变方法，或者基于已获得的信息直接给出结论。"
)

# R21 dsh 范式吸收「目标状态与推进权分离」②：一个"目标"这一轮是否还能继续跑，和
# "这一轮为什么能继续"应该分开记录，而不是只有一个隐式的轮次上限常量。下面把
# run_stage_tool_loop 里真实存在的两条推进路径（读代码确认，未臆造第三种）落成
# 显式取值，写入 Trace 的 advancement_basis 字段：
#   - within_round_budget：仍在 max_rounds 预算内，本轮正常进入
#   - round_budget_exhausted：轮次预算耗尽（for-else 分支），强制中止工具调用并做
#     一次性收尾合成
# 取值故意不跨模块 import（仅 2 个字符串常量，review_pass.py 有同名对应常量，取值
# 必须保持字面一致）——为 2 个字符串新增模块耦合没有必要（YAGNI）。
ADVANCE_WITHIN_ROUND_BUDGET = "within_round_budget"
ADVANCE_ROUND_BUDGET_EXHAUSTED = "round_budget_exhausted"


def _tool_call_signature(fn_name: str, fn_args: dict) -> str:
    """稳定的 (工具名, 参数) 签名：参数排序后 json 序列化再取 sha256，用于跨轮比较是否
    为"完全相同"的调用（R21 循环卫生①）。序列化失败（不可序列化对象）时退化为 repr，
    不静默丢弃比较能力（公理3）。"""
    try:
        args_repr = json.dumps(fn_args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        args_repr = repr(fn_args)
    return hashlib.sha256(f"{fn_name}:{args_repr}".encode("utf-8")).hexdigest()


def extract_json_object(content: str) -> Optional[dict]:
    """Robustly extract a single JSON object from an LLM answer (批2).

    The P0-P3 tool loop lets the model reason across rounds; its final answer is often
    grounded JSON wrapped in prose and/or a ```json fence (e.g. "基于事实包推理：\n```json\n{…}\n```").
    The stages' _parse only tolerated a fence at the very START, so such prose-prefixed
    (but fully grounded) output fell through to parse_error → empty產物 (regression).

    Extraction order (contract-preserving — never fabricates content):
      1. direct json.loads
      2. a ```json … ``` (or ``` … ```) fenced block anywhere
      3. the outermost {…} span (first '{' to last '}')
    Returns the dict, or None when nothing parses (caller keeps its honest parse_error path).
    """
    text = (content or "").strip()
    if not text:
        return None
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            d = json.loads(text[i:j + 1])
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return None


def _trace_round_advance(tracer, *, project_id: str, run_id: str, stage: str,
                         round_no: int, max_rounds: int, basis: str, summary: str) -> None:
    """把"这一轮为什么能继续"写入既有 Trace 机制（R21 ②）：只追加一条记录，不新建
    Trace 通道，也不参与/影响是否推进的决定——LangGraph/本循环仍是唯一的推进者，这里
    只是在已有的推进点旁边补一条可回答"谁/为何推进本轮"的记录。advisory：写入失败不
    影响循环本身（与 node_loop._trace 的静默失败记录方式一致）。"""
    if tracer is None:
        return
    try:
        tracer.write("stage_tool_loop", action=basis, summary=summary,
                     project_id=project_id, run_id=run_id or None, stage=stage,
                     round=round_no, max_rounds=max_rounds, advancement_basis=basis)
    except Exception:
        logger.debug("stage_tool_loop trace 写入失败（advisory）", exc_info=True)


async def run_stage_tool_loop(
    gateway,
    *,
    system_content: str,
    user_content: str,
    project_id: str,
    run_id: str = "",
    stage: str = "p0",
    strategy_id: str = "system-default",
    max_tokens: Optional[int] = None,
    temperature: float = 0.3,
    timeout: Optional[float] = DEFAULT_STAGE_TIMEOUT_SECONDS,
    tracer=None,
    max_rounds: int = _MAX_TOOL_ROUNDS,
) -> dict:
    """Run a bounded multi-round tool-calling loop for a P-stage agent.

    `max_tokens` 默认 **None = 不设平台侧输出上限**（V26.2 返工批次二，用户裁决 Q-B2-1）：
    None 会一路透传到 adapter，请求体里根本不出现该键（不是传 `max_tokens: None`）。调用方
    仍**可以**显式传一个上限作为逃生阀 —— 只是默认不传。上限交还给模型自身最大输出长度与
    adapter 的三层时间护栏（本批次不得放宽那三层）。

    `max_rounds` defaults to `_MAX_TOOL_ROUNDS` (6) but MAY be overridden per call/run
    by the caller — an explicit optional parameter, not a global config knob (R21).
    Callers that omit it keep today's behaviour unchanged.

    Returns a dict:
      {"status": "completed"|"failed", "content": str, "model_used": str|None,
       "tool_rounds": int, "tools_invoked": [name, ...],
       "attempted_chain": [...], "error_category": str, "error_message": str}

    The agent reads the real source on demand via the full toolset; its FINAL text
    (the round with no further tool calls, or the forced synthesis after the round
    budget is spent) is returned as `content` for the caller to parse into its contract.

    Falls back to a single `gateway.call(...)` when the gateway has no `call_stream`
    (test doubles) — the returned shape is identical.
    """
    messages = [{"role": "system", "content": system_content},
                {"role": "user", "content": user_content}]

    # Fallback for injected test gateways without streaming/tools: single call. Preserves
    # the pre-batch2 blocked/parse test paths (no mock in the production path — the real
    # gateway always has call_stream).
    if not hasattr(gateway, "call_stream"):
        result = await gateway.call(
            messages=messages, strategy_id=strategy_id, max_tokens=max_tokens,
            temperature=temperature, source="api", project_id=project_id,
            run_id=run_id, stage=stage, **({"timeout": timeout} if timeout else {}))
        if result.get("status") != "completed":
            return {"status": "failed", "content": "",
                    "model_used": result.get("model"),
                    "tool_rounds": 0, "tools_invoked": [],
                    "attempted_chain": result.get("attempted_chain", []),
                    "error_category": result.get("error_category", "model_unavailable"),
                    "error_message": (result.get("error_message")
                                      or result.get("error_category") or "model_call_failed")}
        return {"status": "completed", "content": result.get("content", ""),
                "model_used": result.get("model"), "tool_rounds": 0,
                "tools_invoked": [], "attempted_chain": result.get("attempted_chain", []),
                "error_category": "", "error_message": ""}

    # Full toolset (not stage-filtered): all enabled Registry tools + MCP + builtins.
    tools = _load_tools(stage)

    final_text = ""
    model_used: Optional[str] = None
    tools_invoked: list[str] = []
    rounds_used = 0
    # R21 循环卫生①：跨轮追踪每个 (工具名+参数) 签名的"连续调用轮数"。
    prev_round_signatures: set[str] = set()
    repeat_streaks: dict[str, int] = {}
    try:
        for _round in range(max_rounds):
            rounds_used = _round + 1
            _trace_round_advance(
                tracer, project_id=project_id, run_id=run_id, stage=stage,
                round_no=rounds_used, max_rounds=max_rounds,
                basis=ADVANCE_WITHIN_ROUND_BUDGET,
                summary=f"stage tool-loop round {rounds_used}/{max_rounds} for {stage}（预算内推进）")
            round_tool_calls: list[dict] = []
            round_text = ""
            async for frame in gateway.call_stream(
                    messages=messages, max_tokens=max_tokens, temperature=temperature,
                    tools=tools, source="api", project_id=project_id,
                    run_id=run_id, stage=stage,
                    strategy_id=strategy_id, timeout=timeout):
                ftype = frame.get("type")
                if ftype == "token":
                    round_text += frame.get("content", "")
                elif ftype == "tool_calls":
                    _accumulate_tool_calls(round_tool_calls, frame["tool_calls"])
                elif ftype == "done":
                    model_used = frame.get("model") or frame.get("selected_model") or model_used
                    break
                elif ftype == "error":
                    return {"status": "failed", "content": "", "model_used": model_used,
                            "tool_rounds": rounds_used, "tools_invoked": tools_invoked,
                            "attempted_chain": frame.get("attempted_chain", []),
                            "error_category": frame.get("error_category", "model_unavailable"),
                            "error_message": (frame.get("error_message")
                                              or frame.get("error_category") or "模型流式调用失败")}

            # No tool calls this round → the round text is the model's answer.
            if not round_tool_calls or not round_tool_calls[0].get("id"):
                final_text = round_text
                break

            # Execute tool calls (L0-L5 enforced inside execute_tool) and feed results back.
            messages.append({"role": "assistant", "content": round_text or None,
                             "tool_calls": round_tool_calls})
            current_signatures: set[str] = set()
            for tc in round_tool_calls:
                if not tc.get("id"):
                    continue
                fn_name = tc["function"]["name"]
                tools_invoked.append(fn_name)
                try:
                    fn_args = (json.loads(tc["function"]["arguments"])
                               if tc["function"]["arguments"].strip() else {})
                except json.JSONDecodeError:
                    fn_args = {}
                current_signatures.add(_tool_call_signature(fn_name, fn_args))
                result = await _run_tool(fn_name, fn_args, project_id, stage, run_id, tracer)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})

            # R21 循环卫生①：连续同签名调用达阈值 → 下一轮消息里注入通用提醒（不拦截本次调用）。
            next_streaks: dict[str, int] = {}
            max_streak = 0
            for sig in current_signatures:
                streak = repeat_streaks.get(sig, 0) + 1 if sig in prev_round_signatures else 1
                next_streaks[sig] = streak
                max_streak = max(max_streak, streak)
            repeat_streaks = next_streaks
            prev_round_signatures = current_signatures
            if max_streak >= _REPEAT_TOOL_CALL_THRESHOLD:
                messages.append({"role": "user",
                                 "content": _REPEAT_TOOL_CALL_REMINDER_TEMPLATE.format(n=max_streak)})

        else:
            # Round budget spent while still calling tools → force ONE final synthesis
            # (tools disabled) so the model MUST produce the structured answer from the
            # accumulated real tool results (mirror p4 worker / agent_loop forced-final).
            _trace_round_advance(
                tracer, project_id=project_id, run_id=run_id, stage=stage,
                round_no=rounds_used, max_rounds=max_rounds,
                basis=ADVANCE_ROUND_BUDGET_EXHAUSTED,
                summary=f"stage tool-loop 达到 {max_rounds} 轮上限（{stage}），强制中止工具调用并收尾合成")
            messages.append({"role": "user",
                             "content": "请立即基于以上工具读取到的真实信息，直接输出本阶段要求的最终 JSON 产物，不要再调用工具。"})
            async for frame in gateway.call_stream(
                    messages=messages, max_tokens=max_tokens, temperature=temperature,
                    tools=None, source="api", project_id=project_id, strategy_id=strategy_id,
                    run_id=run_id, stage=stage,
                    timeout=timeout):
                ftype = frame.get("type")
                if ftype == "token":
                    final_text += frame.get("content", "")
                elif ftype == "done":
                    model_used = frame.get("model") or frame.get("selected_model") or model_used
                    break
                elif ftype == "error":
                    return {"status": "failed", "content": "", "model_used": model_used,
                            "tool_rounds": rounds_used, "tools_invoked": tools_invoked,
                            "attempted_chain": frame.get("attempted_chain", []),
                            "error_category": frame.get("error_category", "model_unavailable"),
                            "error_message": (frame.get("error_message")
                                              or frame.get("error_category") or "模型流式调用失败")}
    except Exception as e:
        logger.warning("stage tool-loop failed for stage=%s project=%s", stage, project_id,
                       exc_info=True)  # 公理3
        return {"status": "failed", "content": "", "model_used": model_used,
                "tool_rounds": rounds_used, "tools_invoked": tools_invoked,
                "attempted_chain": [], "error_category": "model_call_exception",
                "error_message": f"模型工具循环失败：{e}"}

    if not (final_text or "").strip():
        return {"status": "failed", "content": "", "model_used": model_used,
                "tool_rounds": rounds_used, "tools_invoked": tools_invoked,
                "attempted_chain": [], "error_category": "empty_content",
                "error_message": "模型工具循环未产出有效内容"}
    return {"status": "completed", "content": final_text, "model_used": model_used,
            "tool_rounds": rounds_used, "tools_invoked": tools_invoked,
            "attempted_chain": [], "error_category": "", "error_message": ""}


def _load_tools(stage: str) -> list[dict]:
    """Load the full enabled toolset (not stage-filtered) as OpenAI-format schemas,
    stripping internal (_-prefixed) keys the provider rejects."""
    from app.services.tool_registry import load_schemas
    from app.core.database import get_session
    db = get_session()
    try:
        raw_tools = load_schemas(stage=stage, db=db, include_mcp=True)
    finally:
        db.close()
    return [{k: v for k, v in t.items() if not k.startswith("_")} for t in raw_tools]


def _accumulate_tool_calls(round_tool_calls: list[dict], frame_calls) -> None:
    """Accumulate streamed tool-call deltas into the round buffer (mirror p4 worker)."""
    for tc in frame_calls:
        idx = tc.index if hasattr(tc, "index") else 0
        while len(round_tool_calls) <= idx:
            round_tool_calls.append({"id": "", "type": "function",
                                     "function": {"name": "", "arguments": ""}})
        if getattr(tc, "id", ""):
            round_tool_calls[idx]["id"] = tc.id
        if tc.function:
            if tc.function.name:
                round_tool_calls[idx]["function"]["name"] = tc.function.name
            if tc.function.arguments:
                round_tool_calls[idx]["function"]["arguments"] += tc.function.arguments


async def _run_tool(fn_name: str, fn_args: dict, project_id: str, stage: str,
                    run_id: str, tracer) -> dict:
    """Execute one tool via tool_registry.execute_tool (L0-L5 enforced). confirmed=False:
    L3+ tools park behind the existing action_approval Gate — the platform risk grading
    owns safety, not a per-stage whitelist (D-110)."""
    try:
        from app.services.tool_registry import execute_tool
        from app.core.database import get_session
        db = get_session()
        try:
            return await execute_tool(fn_name, fn_args, project_id, stage=stage,
                                      db=db, tracer=tracer, run_id=run_id)
        finally:
            db.close()
    except Exception as e:
        logger.warning("stage tool execution failed for %s: %s", fn_name, e)  # 公理3
        return {"error": f"工具执行失败: {e}"}
