"""AgentLoop — R9-3G-C/P0-5: Agent conversation with true SSE streaming + tool calling.

T5: Model calls routed through ModelGateway.call_stream() — no direct litellm import.
T2.4/R9-5-4: Tools loaded from ToolRegistry (seed-driven) — no hardcoded AGENT_TOOLS list.
Supports tool calling: get_project_info / read_artifact / run_profiling (+ any Registry tools).
No LangGraph checkpointing (reserved for R10).
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from app.services.mode_policy import authorize_action, risk_for_action, RISK_ORDER

logger = logging.getLogger("rebuild.agent_loop")


def _rank(risk: str) -> int:
    try:
        return RISK_ORDER.index((risk or "L1").upper())
    except ValueError:
        return RISK_ORDER.index("L1")


# Controlled-action floor: tools at or above this risk go through mode-aware
# authorization (Manual HITL / three-mode). Below it (L0-L1 reads) execute freely.
_CONTROLLED_FLOOR = RISK_ORDER.index("L2")


class AgentLoop:
    """Agent conversation loop with true SSE streaming + tool calling via ModelGateway."""

    def __init__(self):
        self._tools = None   # loaded lazily per run() call via tool_registry

    async def run(
        self,
        message: str,
        *,
        project_id: str,
        project_name: str = "",
        stage: str = "p0",
        mode: str = "plan",
        run_id: str = "",
        confirm: bool = False,
        artifacts: Optional[list[str]] = None,
        profiling_summary: Optional[str] = None,
        recent_traces: Optional[list[dict]] = None,
        file_count: int = 0,
        # UX-3: specialist-agent routing + conversation history replay.
        agent_type: str = "node_worker",
        history: Optional[list[dict]] = None,
    ) -> AsyncIterator[str]:
        """Run agent chat with tool calling capability.

        R9-5-7 T10/T13: controlled tool calls (risk ≥ L2) pass through
        mode_policy.authorize_action. In Manual mode (or Plan-out-of-plan /
        Auto high-risk), an action that needs confirmation is parked behind a
        real action_approval Gate and surfaced via a `gate.request` SSE event —
        the user approves via the Gate decision endpoint and re-sends with
        confirm=True (mirrors the terminal execute_command pattern). No fake
        chat bubble stands in for execution (D-087 / STOP-4).

        UX-3: agent_type selects the specialist persona (responsibilities/forbidden) via
        context_assembler.build_system_prompt; history replays prior conversation turns so
        the specialist remembers the dialogue across turns.
        """
        # T2.4/R9-5-4: load tool schemas from ToolRegistry (seed-driven, built-ins as fallback)
        try:
            from app.services.tool_registry import load_schemas
            from app.core.database import get_session
            db = get_session()
            try:
                tools = load_schemas(stage=stage, db=db, include_mcp=True)
            finally:
                db.close()
            # Strip private _ keys before sending to LLM
            self._tools = [
                {k: v for k, v in t.items() if not k.startswith("_")}
                for t in tools
            ]
            self._tool_meta = {
                t["function"]["name"]: t
                for t in tools
            }
        except Exception as _te:
            logger.warning("tool_registry.load_schemas failed, using builtin fallback: %s", _te)
            from app.services.tool_registry import _BUILTIN_SCHEMAS
            self._tools = [
                {k: v for k, v in t.items() if not k.startswith("_")}
                for t in _BUILTIN_SCHEMAS
            ]
            self._tool_meta = {t["function"]["name"]: t for t in _BUILTIN_SCHEMAS}

        # T-10/R9-5-3: build system_prompt via unified context assembler (C0-C6 + SKILL.md body)
        # instead of the former hardcoded _build_prompt string concatenation.
        try:
            from app.services.context_assembler import build_system_prompt
            from app.services.project_service import ProjectService
            # R20-2-04（本轮新发现的第 15 处断链，§5.2）：此前只传 {"name": project_name}
            # 1 键，scenario 到不了 Workspace 对话路径。build_project_context_dict 失败/项目
            # 不存在时退化为原 1 键 dict（诚实降级，行为不劣于此前现状）。
            project_dict = ProjectService.build_project_context_dict(project_id) or {"name": project_name}
            run_dict = {"execution_mode": mode} if mode else None
            node_state = {}
            if profiling_summary:
                node_state["upstream_output"] = profiling_summary[:1000]
            if recent_traces:
                trace_lines = [f"{t.get('trace_type','')}:{t.get('summary','')}" for t in recent_traces[:5]]
                node_state["upstream_output"] = (node_state.get("upstream_output", "") + "\n最近活动:" + "; ".join(trace_lines)).strip()
            system_prompt = build_system_prompt(
                project_id, stage,
                project=project_dict, run=run_dict, node_state=node_state or None,
                task_type="chat", agent_type=agent_type,
            )
            # Append dynamic fields not captured by C0-C6 layers
            if file_count > 0:
                system_prompt += f"\n\n项目源码: {file_count} 个文件已导入到 workspace/source/"
            if artifacts:
                system_prompt += f"\n阶段产物: {', '.join(artifacts[:20])}"
        except Exception as _e:
            logger.warning("build_system_prompt failed, using fallback: %s", _e)
            system_prompt = self._build_prompt_fallback(
                project_name, project_id, stage, mode, file_count, profiling_summary, artifacts, recent_traces)

        # R11-3 FIX: the weak default model sometimes NARRATES intent ("好的，我先获取项目
        # 信息…") and then stops WITHOUT emitting a tool call — leaving the user with a
        # preamble and no real answer. Instruct it explicitly to call tools rather than
        # announce them, and to base answers on real tool results (公理: No Evidence No Answer).
        system_prompt += (
            "\n\n【工具使用纪律】当你需要项目真实信息、阶段状态、产物内容或需要执行受控动作时，"
            "必须直接调用相应工具获取真实数据后再作答；禁止只声称'我将获取/我先查看'却不实际调用工具。"
            "回答必须基于工具返回的真实结果，不得脑补或编造项目数据。"
        )

        messages = [{"role": "system", "content": system_prompt}]
        # UX-3: replay prior conversation turns so the specialist remembers the dialogue.
        for h in history or []:
            if not h or not isinstance(h, dict):
                continue
            role = h.get("role")
            if role == "tool":
                messages.append({
                    "role": "tool",
                    "tool_call_id": h.get("tool_call_id", ""),
                    "content": h.get("content", ""),
                })
            elif role in ("user", "agent", "assistant"):
                messages.append({"role": role, "content": h.get("content", "")})
        messages.append({"role": "user", "content": message})

        full_response = ""
        try:
            from app.dependencies import get_services
            gateway = get_services().model_gateway

            # ── Multi-round tool calling loop ──
            # R11-3 FIX: was 3 — too small once the model calls several tools (e.g.
            # get_project_info + list_files + multiple read_artifact), it spent every
            # round calling tools and never reached a synthesis round → user got only
            # the preamble. Raised, plus a forced final synthesis (for/else below).
            max_rounds = 6
            for round_num in range(max_rounds):
                round_tool_calls: list[dict] = []

                async for frame in gateway.call_stream(
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.7,
                    tools=self._tools,
                    source="api",
                    project_id=project_id,
                ):
                    ftype = frame.get("type")

                    if ftype == "token":
                        full_response += frame["content"]
                        yield f"event: delta\ndata: {json.dumps({'token': frame['content'], 'done': False}, ensure_ascii=False)}\n\n"

                    elif ftype == "tool_calls":
                        for tc in frame["tool_calls"]:
                            idx = tc.index if hasattr(tc, "index") else 0
                            while len(round_tool_calls) <= idx:
                                # OpenAI/litellm require "type":"function" on each tool_call
                                # in the assistant message fed back for the follow-up round.
                                # Omitting it made the post-tool completion come back EMPTY
                                # (provider accepted the malformed message but returned no
                                # content) — the agent produced no final answer. (R11-3 FIX)
                                round_tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if hasattr(tc, "id") and tc.id:
                                round_tool_calls[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    round_tool_calls[idx]["function"]["name"] = tc.function.name
                                if tc.function.arguments:
                                    round_tool_calls[idx]["function"]["arguments"] += tc.function.arguments

                    elif ftype == "done":
                        break

                    elif ftype == "error":
                        error_msg = f"[模型调用失败: {frame.get('error_message', frame.get('error_category', ''))}]"
                        full_response += error_msg
                        yield f"event: delta\ndata: {json.dumps({'token': error_msg, 'done': True, 'summary': error_msg}, ensure_ascii=False)}\n\n"
                        return

                # No tool calls → done
                if not round_tool_calls or not round_tool_calls[0].get("id"):
                    break

                # Execute tools, yield progress, and feed results back to messages
                messages.append({"role": "assistant", "content": None, "tool_calls": round_tool_calls})
                gate_pending = False
                for tc in round_tool_calls:
                    if not tc.get("id"):
                        continue
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"].strip() else {}
                    except json.JSONDecodeError:
                        fn_args = {}

                    # ── R9-5-7 T10/T13: mode-aware authorization (Manual HITL / three-mode) ──
                    risk = risk_for_action(fn_name)
                    if _rank(risk) >= _CONTROLLED_FLOOR:
                        authz = authorize_action(mode, risk, fn_name, confirmed=confirm)
                        if authz["decision"] == "require_confirmation":
                            # Park the action behind a REAL Gate; surface via gate.request.
                            gate_id = self._create_action_gate(
                                project_id, run_id, stage, fn_name, risk, authz["reason"],
                                fn_args)
                            yield ("event: gate.request\ndata: " + json.dumps({
                                "gate_id": gate_id, "action": fn_name, "risk_level": risk,
                                "mode": mode,
                                "summary": f"Agent 拟执行受控动作 {fn_name}（{risk}）：{authz['reason']}",
                            }, ensure_ascii=False) + "\n\n")
                            messages.append({"role": "tool", "tool_call_id": tc["id"],
                                             "content": json.dumps(
                                                 {"status": "awaiting_approval", "gate_id": gate_id,
                                                  "message": f"动作 {fn_name} 待用户确认（{mode}/{risk}）"},
                                                 ensure_ascii=False)})
                            gate_pending = True
                            continue

                    # Authorized (auto_approved / approved) or low-risk read → execute
                    result = await self._execute_tool(fn_name, fn_args, project_id, project_name, stage, file_count, artifacts, run_id=run_id)
                    yield f"event: tool\ndata: {json.dumps({'tool': fn_name, 'result': str(result)[:200]}, ensure_ascii=False)}\n\n"
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})

                if gate_pending:
                    # Stop the loop: await the user's Gate decision. The frontend approves
                    # via POST /gates/{id}/decision then re-sends with confirm=True to resume.
                    yield f"event: done\ndata: {json.dumps({'done': True, 'gate_pending': True, 'summary': '等待用户确认受控动作'}, ensure_ascii=False)}\n\n"
                    return
                # Loop continues — next round may call more tools or return text
            else:
                # R11-3 FIX: all rounds were spent CALLING tools (e.g. reading many
                # artifacts) without a final text round → the user would get only the
                # preamble and no answer. Force one final synthesis with tools disabled
                # so the model MUST produce the answer from the accumulated tool results.
                async for frame in gateway.call_stream(
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.7,
                    tools=None,
                    source="api",
                    project_id=project_id,
                ):
                    ftype = frame.get("type")
                    if ftype == "token":
                        full_response += frame["content"]
                        yield f"event: delta\ndata: {json.dumps({'token': frame['content'], 'done': False}, ensure_ascii=False)}\n\n"
                    elif ftype == "done":
                        break
                    elif ftype == "error":
                        error_msg = f"[模型调用失败: {frame.get('error_message', frame.get('error_category', ''))}]"
                        full_response += error_msg
                        yield f"event: delta\ndata: {json.dumps({'token': error_msg, 'done': True, 'summary': error_msg}, ensure_ascii=False)}\n\n"
                        return

        except Exception as e:
            logger.error(f"Agent streaming failed: {e}")
            error_msg = f"[Agent 调用失败: {str(e)}]"
            yield f"event: delta\ndata: {json.dumps({'token': error_msg, 'done': True, 'summary': error_msg}, ensure_ascii=False)}\n\n"
            return

        yield f"event: done\ndata: {json.dumps({'done': True, 'summary': full_response[:300]}, ensure_ascii=False)}\n\n"

    def _create_action_gate(self, project_id: str, run_id: str, stage: str,
                            fn_name: str, risk: str, reason: str,
                            args: dict | None = None) -> str:
        """Create a real action_approval Gate for a parked controlled action (T10).

        Reuses GateService.create — the same DB-persisted + audited Gate kernel as
        stage_promotion (公理6). action_approval Gates do NOT drive stage promotion.

        `args`（B-R20-GATE-NO-PAYLOAD，用户 2026-09-06 批准）：工具入参经
        `tool_registry._redacted_action_payload` **脱敏后**写入 summary，使审批者可知情决策。
        与 `tool_registry._create_risk_gate` 共用同一个脱敏渲染器（单一事实源，避免两处措辞漂移）。

        B-ACC-GATE-APPROVAL-NOT-BOUND：同时持久化**被审阅入参的指纹**。本方法创建的 Gate
        正是 `tool_registry._resolve_action_gate` 会拿去授权 re-dispatch 的那一类，若这里不写
        指纹，比对必然不通过（fail-closed）⇒ 用户批准后工具仍会再弹一次 Gate。指纹用与
        tool_registry 完全相同的那一个函数计算（`action_args_fingerprint`），不另算一份。
        """
        try:
            from app.dependencies import get_services
            from app.services.tool_registry import (
                _redacted_action_payload, action_args_fingerprint)
            payload = _redacted_action_payload(fn_name, args)
            gate = get_services().gate_service.create(
                project_id=project_id, run_id=run_id or "", stage=stage,
                gate_type="action_approval",
                reason=reason or f"Manual/HITL 授权：Agent 拟执行 {fn_name}",
                risk_level=risk,
                summary=(f"Agent 拟执行受控动作 {fn_name}（{risk}）。"
                         f"\n待执行入参（已脱敏）：{payload}"),
                options=["approve", "reject"],
                action_fingerprint=action_args_fingerprint(fn_name, args),
            )
            return gate.gate_id
        except Exception as e:
            logger.warning("action_approval gate create failed for %s: %s", fn_name, e)
            return ""

    def _build_prompt_fallback(self, project_name, project_id, stage, mode, file_count, profiling_summary, artifacts, recent_traces):
        parts = [
            "你是 rebuild 平台的 AI 助手，负责协助用户完成软件迁移项目的接入和建档。",
            f"当前项目: {project_name} (ID: {project_id})",
            f"当前阶段: {stage}",
            f"执行模式: {mode}",
        ]
        if file_count > 0:
            parts.append(f"项目源码: {file_count} 个文件已导入到 workspace/source/")
        if profiling_summary:
            parts.append(f"项目技术栈信息:\n{profiling_summary[:2000]}")
        if artifacts:
            parts.append(f"阶段产物: {', '.join(artifacts[:20])}")
        if recent_traces:
            trace_lines = [f"- {t.get('trace_type', '')}: {t.get('summary', '')}" for t in recent_traces[:5]]
            if trace_lines:
                parts.append("最近活动:\n" + "\n".join(trace_lines))
        return "\n\n".join(parts)

    async def _execute_tool(self, name: str, args: dict, project_id: str, project_name: str,
                            stage: str, file_count: int, artifacts: list[str] | None,
                            run_id: str = "") -> dict:
        """Execute a tool call — routes via tool_registry.execute_tool() (T2.4/R9-5-4)."""
        try:
            from app.services.tool_registry import execute_tool
            from app.core.database import get_session
            db = get_session()
            try:
                return await execute_tool(name, args, project_id, stage=stage, db=db, run_id=run_id)
            finally:
                db.close()
        except Exception as e:
            logger.warning("tool_registry.execute_tool failed for %s: %s", name, e)
            return {"error": f"工具执行失败: {e}"}
