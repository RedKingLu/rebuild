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

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("rebuild.stage_agent_loop")

_MAX_TOOL_ROUNDS = 6  # bounded tool-calling rounds (mirror p4 worker / agent_loop)


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
    try:
        for _round in range(max_rounds):
            rounds_used = _round + 1
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
                result = await _run_tool(fn_name, fn_args, project_id, stage, run_id, tracer)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
        else:
            # Round budget spent while still calling tools → force ONE final synthesis
            # (tools disabled) so the model MUST produce the structured answer from the
            # accumulated real tool results (mirror p4 worker / agent_loop forced-final).
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
