"""P1 original acceptance-baseline capture service (R17.5 P1 WP-B / D-106).

D-106: at P1建档 the platform must capture the ORIGINAL acceptance baseline (regression
baseline / characterization tests) so P5 can do行为等价/回归对比. Per AGENTS §2.3 the split is:
  · STATIC baseline (LLM推理, 必产): test盘点+断言解读 / 缺测路径识别 / 特征化测试规格生成.
  · DYNAMIC golden (确定性 verify, 用户裁决必须产=必须真跑真捕获): run the ORIGINAL tests /
    key scenarios via ExecutionProvider and capture the golden output.
      - 本地可跑栈 (Python/Node/Java/Go on Linux) → 直接跑捕获.
      - 不可在本平台跑的栈 (信创/.NET/Windows/SQL Server) → 路由到可运行环境 (容器/R14);
      - 真无可运行环境 → 诚实 needs_env / blocked，绝不伪造黄金 (D-097/公理3 红线不可破).

Command/environment SELECTION is a sample-level decision → done by the LLM (which run
command, which language, whether runnable here). EXECUTION+capture is deterministic
(ExecutionProvider). This keeps §2.3 intact: 确定性只在采集与验证.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("rebuild.acceptance_baseline_service")

# D-06（V26.2 总验收真实规模真跑，2026-09-10）：真实项目（1018 文件 / 197,447 行）上本调用的
# completion **触顶原硬编码 max_tokens=6144**（call_log `call_5aa2351f4c19` completion_tokens=6144
# = 上限），静态基线 JSON 中途截断 → `_parse` 的 json.loads 报
# `Unterminated string starting at: line 131 column 15 (char 7513)` → 静态基线退化为空壳。
# 与 R11-7（B-P3-NO-TASKPLANS）的 max_tokens 截断根因**同族**，R11-7 只覆盖了 planning。
# 这里**复用 planning 已建立的 env 可调范式**（`_PLANNING_MAX_TOKENS`/`_PLANNING_TIMEOUT` 经
# ModelGateway.call → LiteLLMAdapter.complete 的 timeout 形参透传），不新造机制。
#
# 默认值依据（实测，非拍脑袋）：
#   · 16384 = 触顶值 6144 的 2.67 倍。本项目一次**完整**产出的静态基线约 6.7K 字符
#     （≈3.6K token，含 5 test_assertions / 10 missing_test_paths / 7 characterization_specs），
#     故 16384 对该规模留约 4 倍余量；同时与同阶段 P1 兄弟调用（profiling / tech_selection /
#     assessment / intake 均 16384）口径一致，不另立门户。**不设无上限**：本产物是有界清单
#     （测试断言 + 缺测路径 + 特征化规格），比 P3 task_plans 小得多，故取 planning 的 32768 的一半。
#   · 240s 与 planning 同值。原路径无 timeout 形参 → 落到 adapter 的 fail-fast 默认 60s，而
#     真实规模下本调用为单次非流式大 JSON 生成（实测同项目 P3 单次调用 60-120s），60s 明显偏紧。
# 二者**只调请求超时与输出预算，不涉及模型/endpoint 选择**（策略仍由 ModelGateway 解析，D-098）。
_BASELINE_MAX_TOKENS = int(os.environ.get("P1_BASELINE_MAX_TOKENS", "16384"))
_BASELINE_TIMEOUT = float(os.environ.get("P1_BASELINE_TIMEOUT", "240"))

_STATIC_SYSTEM_PROMPT = (
    "你是 rebuild 平台的 P1 原始验收基准捕获 Agent（Node Worker Agent，D-106）。平台已采集了目标项目的"
    "测试目录/框架候选与其脱敏原文、依赖清单、主语言/技术栈。你的职责是产出【原始验收基准】的静态部分，"
    "并为动态黄金输出捕获规划【跑原始的命令】。严格输出 JSON，键为：\n"
    "  test_assertions（数组，每项 {path, what_it_verifies}：读已有测试的断言，理解每个测试文件「验证了"
    "什么」；无测试则空数组）；\n"
    "  missing_test_paths（数组，每项 {path_or_module, why_critical}：据业务模块/入口/DB 识别关键但无测试"
    "覆盖的路径）；\n"
    "  characterization_specs（数组，每项 {target, input, expected_output_anchor, rationale}：为缺测关键"
    "路径设计特征化/golden 测试规格——输入→期望输出锚点，供迁移后回归对比）；\n"
    "  dynamic_golden_plan（对象 {runnable_on_platform: bool, language, run_command, working_subdir, "
    "reason, needs_env: {kind, note}}：判断该项目的原始测试/关键场景能否在本 Linux 平台直接真跑（"
    "Python/Node/Java/Go 通常可；.NET Framework/Windows/SQL Server 不可→runnable_on_platform=false 且 "
    "needs_env 说明需何种环境如 windows/.net-framework/sqlserver，可经 R14 远程）。run_command 为在项目根"
    "目录执行的单条 shell 命令（如 `python -m pytest -q` / `npm test` / `go test ./...`），working_subdir "
    "为相对源码根的执行子目录（默认空=根）。禁止编造：不确定能否跑就 runnable_on_platform=false 并说明。"
)


@dataclass
class BaselineResult:
    status: str                        # completed / blocked / failed
    reason: str = ""
    baseline: dict = field(default_factory=dict)
    model_used: Optional[str] = None

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "baseline": self.baseline, "model_used": self.model_used}


class AcceptanceBaselineService:
    """Capture the original acceptance baseline. `gateway`/`execution_provider`
    injectable for tests (no mock in the production path)."""

    def __init__(self, *, gateway=None, execution_provider=None, tracer=None, auditor=None):
        self._gateway = gateway
        self._execution_provider = execution_provider
        self.tracer = tracer
        self.auditor = auditor

    def _get_gateway(self):
        if self._gateway is not None:
            return self._gateway
        from app.dependencies import get_services
        return get_services().model_gateway

    async def capture(
        self,
        project_id: str,
        *,
        facts: dict,
        upstream: Optional[dict] = None,
        run_id: Optional[str] = None,
        stage: str = "p1",
        strategy_id: str = "system-default",
        source_path: Optional[str] = None,
        run_dynamic: bool = True,
    ) -> BaselineResult:
        """Static baseline (LLM) + dynamic golden capture (ExecutionProvider真跑或诚实 needs_env)."""
        gw = self._get_gateway()
        readiness = gw.stage_model_readiness(strategy_id=strategy_id)
        if not readiness.get("available"):
            # 静态基线需 LLM；无 Key → blocked（承 P0/P1，不伪造）。
            return BaselineResult(status="blocked",
                                  reason="no_model_key: 原始验收基准的静态部分需 LLM（D-106）")

        messages = [
            {"role": "system", "content": _STATIC_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_prompt(facts, upstream or {})},
        ]
        result = await gw.call(messages=messages, strategy_id=strategy_id,
                               max_tokens=_BASELINE_MAX_TOKENS, temperature=0.3, source="api",
                               timeout=_BASELINE_TIMEOUT,
                               project_id=project_id, run_id=run_id, stage=stage)
        if result.get("status") != "completed":
            reason = result.get("error_message") or result.get("error_category") or "model_call_failed"
            return BaselineResult(status="failed", reason=str(reason), model_used=result.get("model"))

        static_baseline = self._parse(result.get("content", ""), model_used=result.get("model"))
        model_used = result.get("model")

        # ── 动态黄金输出捕获（确定性 verify 层：真跑或诚实 needs_env） ──────────
        plan = static_baseline.get("dynamic_golden_plan") or {}
        dynamic_golden = await self._capture_dynamic_golden(
            project_id, plan, source_path=source_path, run_id=run_id) if run_dynamic else \
            {"captured": False, "reason": "dynamic capture skipped (run_dynamic=False)"}

        baseline = {
            "artifact_type": "acceptance_baseline",
            "project_id": project_id,
            "stage": stage,
            "produced_by": "llm_node_worker_agent + execution_provider",
            "static_baseline": {
                "test_assertions": static_baseline.get("test_assertions", []),
                "missing_test_paths": static_baseline.get("missing_test_paths", []),
                "characterization_specs": static_baseline.get("characterization_specs", []),
            },
            "dynamic_golden": dynamic_golden,
            "model_used": model_used,
            "note": ("原始验收基准（D-106）：静态基线由 LLM 产出；动态黄金经 ExecutionProvider 真跑真捕获，"
                     "不可运行环境诚实标 needs_env（不伪造，D-097/公理3），供 P5 行为等价/回归对比消费"),
        }
        if static_baseline.get("parse_error"):
            baseline["static_parse_error"] = True
            # D-06：把可诊断信息一并落进产物——只有日志会随进程滚走，读产物的人同样需要能
            # 分辨"被截断"还是"输出非法 JSON"（标注须有消费方）。
            baseline["static_parse_diagnosis"] = static_baseline.get("parse_diagnosis", {})
        self._trace("P1 acceptance baseline captured", project_id, run_id, stage)
        return BaselineResult(status="completed", baseline=baseline, model_used=model_used)

    async def _capture_dynamic_golden(self, project_id: str, plan: dict, *,
                                      source_path: Optional[str], run_id) -> dict:
        """真跑原始测试/关键场景并捕获黄金输出；不可运行 → 诚实 needs_env（绝不伪造）。"""
        if not isinstance(plan, dict) or not plan.get("runnable_on_platform"):
            needs_env = (plan or {}).get("needs_env") or {}
            return {
                "captured": False,
                "needs_env": needs_env or {"kind": "unknown",
                                           "note": "LLM 判定本平台不可跑原始测试/场景"},
                "reason": (plan or {}).get("reason", "本平台不可运行该栈，原始黄金输出待路由到可运行环境"
                                                     "（容器/R14）——诚实 needs_env，不伪造黄金"),
                "planned_command": (plan or {}).get("run_command"),
            }
        run_command = (plan.get("run_command") or "").strip()
        if not run_command:
            return {"captured": False, "reason": "runnable_on_platform=true 但 LLM 未给出 run_command",
                    "needs_env": {"kind": "missing_command"}}

        import os
        from pathlib import Path
        if source_path is None:
            from app.services import workspace_service
            source_path = str(workspace_service.workspace_path(project_id) / "source")
        cwd = source_path
        working_subdir = (plan.get("working_subdir") or "").strip().strip("/")
        if working_subdir:
            candidate = Path(source_path) / working_subdir
            if candidate.exists():
                cwd = str(candidate)

        provider = self._execution_provider
        if provider is None:
            from app.services.execution_provider import get_execution_provider
            provider = get_execution_provider("workspace_local")
        try:
            exec_result = await provider.execute(run_command, language="bash", timeout=180, cwd=cwd)
        except Exception as e:
            return {"captured": False, "reason": f"执行捕获异常：{type(e).__name__}",
                    "command": run_command, "needs_env": {"kind": "execution_error"}}

        if exec_result.get("blocked"):
            return {"captured": False, "reason": f"命令被安全策略阻断：{exec_result.get('stderr','')}",
                    "command": run_command, "provider": exec_result.get("provider"),
                    "needs_env": {"kind": "blocked_by_policy"}}

        return {
            "captured": True,
            "command": run_command,
            "language": plan.get("language"),
            "cwd_subdir": working_subdir or ".",
            "exit_code": exec_result.get("exit_code"),
            "stdout": (exec_result.get("stdout") or "")[:20000],
            "stderr": (exec_result.get("stderr") or "")[:8000],
            "provider": exec_result.get("provider"),
            "execution_mode": exec_result.get("execution_mode"),
            "elapsed_ms": exec_result.get("elapsed_ms"),
            "note": ("原始黄金输出真捕获（未做迁移前的基准）；exit_code/stdout 即回归对比锚点。"
                     "注意：若原始测试因缺依赖/环境未就绪而非零退出，此为诚实的原始状态，不代表迁移能力"),
        }

    def _build_user_prompt(self, facts: dict, upstream: dict) -> str:
        blob = json.dumps({
            "primary_language_from_p0": (upstream or {}).get("primary_language"),
            "detected_stack_from_p0": (upstream or {}).get("detected_stack"),
            "test_candidates": facts.get("test_candidates"),
            "dependency_manifests": [{"path": d.get("path")} for d in (facts.get("dependency_manifests") or [])],
            "build_file_candidates": facts.get("build_file_candidates"),
            "ext_language_counts": facts.get("ext_language_counts"),
            "migration_target": (upstream or {}).get("migration_target"),
        }, ensure_ascii=False, default=str)
        if len(blob) > 18000:
            blob = blob[:18000] + "\n…[截断]"
        return ("以下是采集的测试/依赖/技术栈事实（含测试文件脱敏原文候选）与上游 P0 结论。请产出原始验收基准的"
                "静态部分并规划动态黄金捕获命令。严格输出上述 JSON。\n\n" + blob)

    @staticmethod
    def _scan_json_shape(text: str) -> tuple[int, bool]:
        """扫描 JSON 文本的结构收敛状态，返回 (未闭合的括号深度, 是否停在字符串内部)。

        纯诊断用：**不做任何 JSON 修补，也不放宽解析严格性**——只用来区分
        「输出被截断（结构未闭合）」与「输出完整但非法（如键名/引号写错）」这两种
        处置完全不同的失败（前者要加输出预算，后者要改 prompt 契约）。
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

    def _diagnose_parse_failure(self, text: str, err: Exception) -> dict:
        """构造可诊断信息（D-06）：响应字符长度 + 是否疑似截断 + 预算取值。"""
        depth, in_string = self._scan_json_shape(text)
        signals: list[str] = []
        if not text:
            # 空响应也是预算问题的已知形态（R17.5-P4-FIX 批2.8 实测：推理链耗尽 max_tokens 后
            # 最终文本为空）；归入"疑似截断"以给出同一个可操作结论=加预算，措辞保留不确定性。
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
            "decode_error": f"{type(err).__name__}: {err}",
            "tail_snippet": text[-80:] if text else "",
            "max_tokens": _BASELINE_MAX_TOKENS,
            "timeout_s": _BASELINE_TIMEOUT,
            "env_knobs": "P1_BASELINE_MAX_TOKENS / P1_BASELINE_TIMEOUT",
        }

    def _parse(self, content: str, *, model_used: Optional[str] = None) -> dict:
        text = (content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
            diagnosis = {"content_len": len(text), "suspected_truncation": False,
                         "truncation_signals": [], "decode_error": f"not_a_json_object: {type(data).__name__}",
                         "tail_snippet": text[-80:], "max_tokens": _BASELINE_MAX_TOKENS,
                         "timeout_s": _BASELINE_TIMEOUT,
                         "env_knobs": "P1_BASELINE_MAX_TOKENS / P1_BASELINE_TIMEOUT"}
            logger.warning(
                "P1 baseline: LLM 输出是合法 JSON 但不是对象（契约不符，非截断）—— "
                "响应长度=%d 字符, model=%s, 诊断=%s",
                diagnosis["content_len"], model_used, diagnosis)
        except Exception as e:  # noqa: BLE001 — 公理3：不静默，下面按失败模式分级发声
            diagnosis = self._diagnose_parse_failure(text, e)
            if diagnosis["suspected_truncation"]:
                # 截断：可操作结论是「提高输出预算」，不是改 prompt。故用 error 级并直接给出旋钮。
                logger.error(
                    "P1 baseline: LLM 输出**疑似被截断**导致 JSON 解析失败（D-06 同族）—— "
                    "响应长度=%d 字符, 截断信号=%s, 尾部=%r, model=%s, "
                    "本次预算 max_tokens=%s / timeout=%ss（可经 %s 调整）, 解析错误=%s",
                    diagnosis["content_len"], diagnosis["truncation_signals"],
                    diagnosis["tail_snippet"], model_used, diagnosis["max_tokens"],
                    diagnosis["timeout_s"], diagnosis["env_knobs"], diagnosis["decode_error"])
            else:
                # 非截断：结构已收敛却仍解析失败 ⇒ 模型输出了非法 JSON，可操作结论是改 prompt 契约。
                logger.warning(
                    "P1 baseline: LLM 输出**结构已收敛但非法 JSON**（非截断，需修 prompt 契约）—— "
                    "响应长度=%d 字符, 尾部=%r, model=%s, 本次预算 max_tokens=%s / timeout=%ss, "
                    "解析错误=%s",
                    diagnosis["content_len"], diagnosis["tail_snippet"], model_used,
                    diagnosis["max_tokens"], diagnosis["timeout_s"], diagnosis["decode_error"],
                    exc_info=True)
        # 诚实失败：只回可诊断的空基线壳 + parse_error 标记，绝不伪造断言/规格（D-097/公理3）。
        return {"parse_error": True, "raw": text[:2000], "parse_diagnosis": diagnosis,
                "test_assertions": [], "missing_test_paths": [], "characterization_specs": [],
                "dynamic_golden_plan": {"runnable_on_platform": False,
                                        "needs_env": {"kind": "parse_error"},
                                        "reason": "静态基线解析失败，动态捕获跳过"}}

    def _trace(self, summary: str, project_id, run_id, stage) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.write("model_call", action="p1_acceptance_baseline", summary=summary,
                              project_id=project_id, run_id=run_id, stage=stage)
        except Exception:
            logger.debug("P1 baseline trace 写入失败（advisory）", exc_info=True)
