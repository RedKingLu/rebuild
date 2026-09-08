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
import re
from typing import Any, Optional

logger = logging.getLogger("rebuild.stage_agent_loop")

_MAX_TOOL_ROUNDS = 6  # bounded tool-calling rounds (mirror p4 worker / agent_loop)

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
    max_tokens: int = 32768,
    temperature: float = 0.3,
    timeout: Optional[float] = 180.0,
    tracer=None,
    max_rounds: int = _MAX_TOOL_ROUNDS,
) -> dict:
    """Run a bounded multi-round tool-calling loop for a P-stage agent.

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
