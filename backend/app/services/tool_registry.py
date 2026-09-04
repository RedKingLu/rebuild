"""Tool Registry — loads tool resources from Registry → generates OpenAI tool schemas
and routes execution via ExecutionProvider (T2.1 + T2.2 / R9-5-4).

Public API:
  load_schemas(tool_refs, stage, db) -> list[dict]   # OpenAI-format tool schemas
  execute_tool(tool_name, args, project_id, db) -> dict

Execution routing by write_scope / binds_via (type_metadata):
  write_scope=none  → local read-only direct execution
  write_scope=workspace → workspace-confined write
  write_scope=system / execute_scope → ExecutionProvider.execute()
  risk_level≥L3    → OD-06: create a real action_approval Gate (GateService) and
                     return awaiting_approval; honest risk_flagged if no Gate backend.

Built-in 3 tools (get_project_info/read_artifact/run_profiling) remain available
as seed-driven tools so capability is not lost when Registry is empty.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("rebuild.tool_registry")

# ── D-114 写盘路径文件名清洗 ────────────────────────────────────────────────
# 模型在 fs_write_artifact 的 path 里常把中文描述/括注写进文件名，产出非法、不可编译的
# 路径（如 `output_code/x/MicroDBHelper.cs（QueryExcel 方法——OleDb 读取）`）。此处逐段清洗。
_WRITE_TOP_DIRS = ("output_code", "artifacts", "patches", "source")
# 合法"文件名.扩展名"后跟中文括注/顿号描述 → 截断到扩展名（如 foo.cs（说明）→ foo.cs）。
_EXT_THEN_JUNK_RE = re.compile(r"^(.*?\.[A-Za-z0-9]{1,10})\s*[（(、，。：:].*$")
# 保留的合法路径段字符（ASCII 标识符 + . - _ 空格→_）。
_ILLEGAL_SEG_RE = re.compile(r"[^A-Za-z0-9._-]+")
# R19-3-01：阶段产物目录前缀（p0…p6）。模型常写 `p1/tech_stack.json` 而漏掉 `artifacts/`
# 顶层；旧版一律前缀成 output_code/，导致真跑 output_code 里混入 p0/p1 的 json（非代码产物）。
_STAGE_DIR_RE = re.compile(r"^p[0-6]$")


def _sanitize_segment(seg: str) -> str:
    seg = (seg or "").strip()
    if not seg or seg in (".", ".."):
        return ""
    m = _EXT_THEN_JUNK_RE.match(seg)          # 截断扩展名后的中文描述
    if m:
        seg = m.group(1)
    # R19-3-01：先把扩展名摘出来再清洗主干。旧版直接对整段 sub + strip("._-")，
    # 主干为纯 CJK 时会被削成只剩扩展名的文件 —— 真跑产物里出现了名为 `cs` 的文件
    # （`集成测试.cs` → `_.cs` → `cs`）。主干清空时用原名摘要兜底，避免同目录多个
    # 中文名文件互相静默覆盖。
    stem, dot, ext = seg.rpartition(".")
    if dot and stem and re.fullmatch(r"[A-Za-z0-9]{1,10}", ext):
        cleaned_stem = _ILLEGAL_SEG_RE.sub("_", stem).strip("._-")
        if not cleaned_stem:
            cleaned_stem = "file_" + hashlib.md5(stem.encode("utf-8")).hexdigest()[:8]
        return f"{cleaned_stem}.{ext}"
    seg = _ILLEGAL_SEG_RE.sub("_", seg).strip("._-")   # 剔除 CJK/空格/标点
    return seg


def _sanitize_write_path(rel: str) -> str:
    """逐段清洗写盘相对路径：顶层白名单目录保持原样，其余段清洗非法字符/截断括注。
    末段（文件名）清洗后为空则回退 file.txt。纯清洗，不改变目录层级/顶层归属。"""
    parts = rel.replace("\\", "/").split("/")
    out = []
    for i, p in enumerate(parts):
        if i == 0 and p in _WRITE_TOP_DIRS:
            out.append(p)
            continue
        cleaned = _sanitize_segment(p)
        if cleaned:
            out.append(cleaned)
    if not out:
        return "output_code/file.txt"
    # 若清洗后只剩顶层目录（其余段全被剥空）→ 补一个兜底文件名
    if len(out) == 1:
        out.append("file.txt")
    return "/".join(out)

# ── Minimal stdlib unified-diff engine (R17.2 apply_patch REC) ─────────────────
# apply_patch previously wrote the draft's "diff" field verbatim as the whole file
# content. If that field is an actual unified diff, writing it raw corrupts the
# target. These helpers detect a unified diff and apply it hunk-by-hunk with strict
# context matching, in pure stdlib (NO patch/whatthepatch/unidiff dependency).
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _looks_like_unified_diff(text: str) -> bool:
    """Heuristic: is `text` a unified diff (vs. a whole-file new content)?

    True when any line is a hunk header (@@ -a,b +c,d @@), a file header
    (--- / +++), or a git diff header (diff --git). Empty/plain content → False
    (so the full-content write path is preserved — backward compatible).
    """
    if not text:
        return False
    for line in text.splitlines():
        if _HUNK_RE.match(line):
            return True
        if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("diff --git"):
            return True
    return False


def apply_unified_diff(base_text: str, diff_text: str) -> str:
    """Apply a unified diff to base_text and return the new content (pure stdlib).

    Locates each hunk by its @@ -a,b +c,d @@ header, copies intervening context
    from the base, verifies every context/deleted line matches the base exactly,
    then emits added lines. Raises ValueError on any mismatch or unappliable hunk
    (D-097: never silently corrupt the target — the caller rejects instead of
    writing garbage).
    """
    base_lines = base_text.splitlines()
    diff_lines = diff_text.splitlines()
    result: list[str] = []
    base_idx = 0  # 0-based cursor into base_lines
    i = 0
    n = len(diff_lines)
    applied_hunks = 0

    while i < n:
        m = _HUNK_RE.match(diff_lines[i])
        if not m:
            # File/metadata headers (---, +++, diff --git, index …) between hunks.
            i += 1
            continue
        start_old = int(m.group(1))
        hunk_start = (start_old - 1) if start_old > 0 else 0
        if hunk_start < base_idx:
            raise ValueError(
                f"hunk 起始行 {start_old} 早于当前处理位置（{base_idx + 1}），diff 顺序异常"
            )
        if hunk_start > len(base_lines):
            raise ValueError(
                f"hunk 起始行 {start_old} 超出 base 文件行数（{len(base_lines)}）"
            )
        # Copy unchanged lines preceding this hunk.
        result.extend(base_lines[base_idx:hunk_start])
        base_idx = hunk_start
        i += 1
        applied_hunks += 1

        # Consume the hunk body until the next hunk/file header.
        while i < n:
            hl = diff_lines[i]
            if _HUNK_RE.match(hl):
                break
            if hl.startswith("--- ") or hl.startswith("+++ ") or hl.startswith("diff --git") or hl.startswith("index "):
                break
            if hl.startswith("\\"):  # "\ No newline at end of file"
                i += 1
                continue
            tag = hl[:1]
            content = hl[1:]
            if tag == " " or hl == "":
                # Context line (a bare empty line is an empty context line).
                if base_idx >= len(base_lines) or base_lines[base_idx] != content:
                    actual = base_lines[base_idx] if base_idx < len(base_lines) else "<EOF>"
                    raise ValueError(
                        f"上下文不匹配 @base行{base_idx + 1}: 期望 {content!r} 实际 {actual!r}"
                    )
                result.append(base_lines[base_idx])
                base_idx += 1
            elif tag == "-":
                if base_idx >= len(base_lines) or base_lines[base_idx] != content:
                    actual = base_lines[base_idx] if base_idx < len(base_lines) else "<EOF>"
                    raise ValueError(
                        f"删除行不匹配 @base行{base_idx + 1}: 期望删除 {content!r} 实际 {actual!r}"
                    )
                base_idx += 1
            elif tag == "+":
                result.append(content)
            else:
                raise ValueError(f"无法识别的 diff 行: {hl!r}")
            i += 1

    if applied_hunks == 0:
        raise ValueError("未找到可应用的 hunk（@@ 段头缺失）")

    # Append the remaining unchanged tail of the base file.
    result.extend(base_lines[base_idx:])
    new_text = "\n".join(result)
    # Preserve a trailing newline when the base had one (or, for an empty base, when
    # the diff content ends with one).
    if base_text.endswith("\n") or (not base_text and diff_text.endswith("\n")):
        new_text += "\n"
    return new_text


def _source_equivalent(out_rel: str) -> str:
    """Map an output_code/ target back to its source/ original (read-only base)."""
    norm = out_rel.replace("\\", "/")
    if norm.startswith("output_code/"):
        return "source/" + norm[len("output_code/"):]
    return ""

# ── Built-in tool fallback (seed-equivalent schemas) ────────────────────────
# These match the hard-coded AGENT_TOOLS in agent_loop.py so the agent always
# has at minimum these three regardless of whether DB seed is populated.
_BUILTIN_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_project_info",
            "description": "获取当前项目的基本信息：阶段、文件数、产物列表",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "_source": "builtin",
        "_tool_id": "builtin:get_project_info",
        "_risk_level": "L0",
        "_write_scope": "none",
    },
    {
        "type": "function",
        "function": {
            "name": "read_artifact",
            "description": "读取指定阶段产物的内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_path": {
                        "type": "string",
                        "description": "产物文件名，如 intake_report.json",
                    },
                },
                "required": ["artifact_path"],
            },
        },
        "_source": "builtin",
        "_tool_id": "builtin:read_artifact",
        "_risk_level": "L0",
        "_write_scope": "none",
    },
    {
        "type": "function",
        "function": {
            "name": "run_profiling",
            "description": "触发 P1 全量识别（14 项技术栈分析），仅在项目已进入 P1 阶段时可用",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "_source": "builtin",
        "_tool_id": "builtin:run_profiling",
        "_risk_level": "L1",
        "_write_scope": "workspace",
    },
    {
        "type": "function",
        "function": {
            "name": "introduce_community_resource",
            "description": (
                "从社区检索资源：按关键词/类型搜索独立社区服务；若该资源已导入并启用则直接返回（不再审批）；"
                "否则创建一道真实的人工审批门（community_resource_introduction），用户同意后才下载(sha256校验)并导入。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（资源名/描述）"},
                    "resource_type": {"type": "string", "description": "资源类型过滤，如 case/tool/skill/knowledge/template"},
                },
                "required": [],
            },
        },
        "_source": "builtin",
        "_tool_id": "builtin:introduce_community_resource",
        "_risk_level": "L2",
        "_write_scope": "none",
    },
    {
        "type": "function",
        "function": {
            "name": "p5_verification_facts",
            "description": (
                "读取本项目 P5 确定性验证的【真实事实】：十槽位状态 + can_be_completed + 条件命令结果，"
                "来源=已落盘的 artifacts/p5_validation_report.json（由确定性验证器产出，非模型自报）。"
                "P5 策略/失败解读用：据此规划验证维度、解读失败根因、提修复建议——"
                "但【不得改写】这些事实，completed 由确定性门禁认定，不由本工具或模型翻转。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "_source": "builtin",
        "_tool_id": "builtin:p5_verification_facts",
        "_risk_level": "L0",
        "_write_scope": "none",
    },
    {
        "type": "function",
        "function": {
            "name": "p5_dimension_capabilities",
            "description": (
                "读取本项目 P5 验证维度的【能力清单 + 环境探测】（capability-first，R17.5-P5-R2）："
                "浏览器/E2E QA、行为等价/断言、DB 结构+数据迁移、业务闭环、回归对 P1 金标准、性能基准、"
                ".NET build/test/static 等维度——每项标 capability_ready（能力是否已接线）与 "
                "environment_available（运行环境是否具备）。这是【非门禁】的确定性能力/环境事实，"
                "不参与 can_be_completed；维度是否适用由你（按 P5 stage skill）判断。环境缺失维度诚实标 "
                "evidence_gap（待环境真验），不伪造通过。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "_source": "builtin",
        "_tool_id": "builtin:p5_dimension_capabilities",
        "_risk_level": "L0",
        "_write_scope": "none",
    },
    {
        "type": "function",
        "function": {
            "name": "p5_verify_dimension",
            "description": (
                "探测单个 P5 验证维度的能力接线与运行环境（确定性事实），供你按本次迁移选维度调用。"
                "dimension 取值：browser_qa / eval_harness / db_migration / business_flow / "
                "regression_baseline / benchmark / dotnet_build。返回 capability_ready + "
                "environment_available + status（available=环境具备可经对应子 skill 工作流产真实事实；"
                "evidence_gap=能力已接线但环境缺失，待环境真验，非阻断）。本工具【只报事实、不产"
                "\"通过\"结论、不改槽位状态、不翻转 can_be_completed】。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "description": ("验证维度名：browser_qa / eval_harness / db_migration / "
                                        "business_flow / regression_baseline / benchmark / dotnet_build"),
                    },
                },
                "required": ["dimension"],
            },
        },
        "_source": "builtin",
        "_tool_id": "builtin:p5_verify_dimension",
        "_risk_level": "L2",
        "_write_scope": "none",
    },
]

# Risk levels that require gate review (T2.3 / S3 note: full HITL接线→R9-5-7)
_GATE_RISK_THRESHOLD = "L3"
_RISK_ORDER = ["L0", "L1", "L2", "L3", "L4", "L5"]


def _rank(risk: str) -> int:
    """Ordinal rank of a risk level string, tolerant of unknown values."""
    try:
        return _RISK_ORDER.index((risk or "L0").upper())
    except ValueError:
        return 0


def load_schemas(
    tool_refs: Optional[list] = None,
    stage: str = "p0",
    db: Optional[Session] = None,
    include_mcp: bool = False,
) -> list[dict]:
    """Return OpenAI-format tool schemas for the agent.

    Resolution order:
    1. If tool_refs provided → load only those specific tool resource_ids
    2. Else → load all enabled active tools from Registry
    3. Merge with built-in schemas (built-ins fill in if Registry has nothing)

    include_mcp: also include tools discovered from connected MCP servers.
    """
    registry_schemas: list[dict] = []

    if db is not None:
        try:
            from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
            q = db.query(ResourceEntry).filter(
                ResourceEntry.resource_type == ResourceType.tool,
                ResourceEntry.enabled == True,
                ResourceEntry.status.in_([ResourceStatus.active, ResourceStatus.read_only]),
            )
            if tool_refs:
                tool_ids = [r if isinstance(r, str) else r.get("tool_id", "") for r in tool_refs]
                tool_ids = [t for t in tool_ids if t and not t.startswith("builtin:")]
                if tool_ids:
                    q = q.filter(ResourceEntry.resource_id.in_(tool_ids))
            tools = q.all()
            for t in tools:
                schema = _build_schema(t)
                if schema:
                    registry_schemas.append(schema)
        except Exception as e:
            logger.warning("tool_registry.load_schemas DB query failed: %s", e)

    # MCP tools injection (T3.4)
    mcp_schemas: list[dict] = []
    if include_mcp and db is not None:
        mcp_schemas = _load_mcp_schemas(db)

    # Merge: registry + MCP + built-ins
    # Avoid duplicates by function name (registry/MCP takes precedence)
    all_schemas = registry_schemas + mcp_schemas
    seen_names = {s["function"]["name"] for s in all_schemas}
    for builtin in _BUILTIN_SCHEMAS:
        if builtin["function"]["name"] not in seen_names:
            all_schemas.append(builtin)

    return all_schemas


def _build_schema(entry) -> Optional[dict]:
    """Build an OpenAI-format tool schema from a ResourceEntry."""
    meta = entry.type_metadata or {}
    input_contract = entry.input_contract or ""

    # Generate parameters from input_contract (JSON schema string) or type_metadata
    params = meta.get("parameters") or meta.get("input_schema")
    if not params and input_contract:
        try:
            import json
            params = json.loads(input_contract)
        except Exception:
            params = None
    if not params:
        params = {"type": "object", "properties": {}, "required": []}

    tool_name = meta.get("tool_name") or entry.name.lower().replace(" ", "_").replace("-", "_")

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": entry.description or entry.name,
            "parameters": params,
        },
        "_source": "registry",
        "_tool_id": entry.resource_id,
        "_risk_level": entry.risk_level.value if hasattr(entry.risk_level, "value") else "L0",
        "_write_scope": meta.get("write_scope", "none"),
        "_binds_via": meta.get("binds_via", ""),
    }


def _load_mcp_schemas(db: Session) -> list[dict]:
    """Load tool schemas from connected MCP servers."""
    try:
        from app.services.mcp_service import MCPService
        svc = MCPService(db)
        servers, _total = svc.list_all()
        schemas = []
        for srv in servers:
            if srv.status != "connected" or not srv.tools:
                continue
            for tool in srv.tools:
                tool_name = f"mcp__{srv.name.lower().replace('-', '_')}__{tool.get('name', '')}"
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"[MCP:{srv.name}] {tool.get('description', '')}",
                        "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}, "required": []},
                    },
                    "_source": "mcp",
                    "_mcp_id": srv.mcp_id,
                    "_mcp_tool_name": tool.get("name", ""),
                    "_risk_level": "L1",
                    "_write_scope": "mcp",
                })
        return schemas
    except Exception as e:
        logger.warning("_load_mcp_schemas failed: %s", e)
        return []


async def execute_tool(
    tool_name: str,
    args: dict,
    project_id: str,
    stage: str = "p0",
    db: Optional[Session] = None,
    tracer=None,
    run_id: str = "",
    confirmed: bool = False,
) -> dict:
    """Execute a tool by name, routing via write_scope / binds_via.

    Routing:
    - builtin:*     → internal dispatch (get_project_info / read_artifact / run_profiling)
    - write_scope=mcp → MCP call_tool()
    - write_scope=none → local read (workspace/source)
    - write_scope=workspace → workspace-confined write to output_code/ or artifacts/
                      via WorkspaceMediator (source/ rejected — D-099①)
    - write_scope=patch_draft → write a patch draft to patches/ via WorkspaceMediator
                      (D-099③ / D-104)
    - write_scope=output_code → apply a patch draft into output_code/ via WorkspaceMediator
    - write_scope=execute/system OR run_safe_command → ExecutionProvider.execute()

    HITL ownership (D-087, WP-C3 / B-R17.2-TOOL-DOUBLEGATE):
    - An L3+ tool requires human approval. There is ONE action_approval Gate per
      (project_id, run_id, tool_name); execute_tool is gate-aware:
        * approved gate found (or caller passes confirmed=True) → re-dispatch = execute
          for real.  This covers both (a) approval of a gate execute_tool created and
          (b) approval of the gate the agent_loop authorization layer created — so
          execute_tool never opens a SECOND gate for an already-approved action.
        * a waiting gate found → return its awaiting_approval (no duplicate gate).
        * no gate → create one, return awaiting_approval.
      confirmed=True lets a caller that already owns the HITL decision (agent_loop /
      REST after gate approval) thread the approved context through directly.

    Writes Trace on every call (公理3 / T2.3).
    """
    # ── Resolve tool entry ────────────────────────────────────────────────
    schema = None
    tool_entry = None
    if db is not None:
        try:
            # Match by tool_name in type_metadata OR by name slug
            from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
            tools = db.query(ResourceEntry).filter(
                ResourceEntry.resource_type == ResourceType.tool,
                ResourceEntry.enabled == True,
            ).all()
            for t in tools:
                meta = t.type_metadata or {}
                slug = meta.get("tool_name") or t.name.lower().replace(" ", "_").replace("-", "_")
                if slug == tool_name:
                    tool_entry = t
                    break
        except Exception:
            # 发声：工具查表的 DB 查询失败若静默会让工具"看似不存在"而非暴露 DB 故障。
            logger.warning("tool_registry: 查询工具资源失败 tool=%s", tool_name, exc_info=True)

    # MCP dispatch
    if tool_name.startswith("mcp__"):
        return await _execute_mcp_tool(tool_name, args, db)

    # Built-in dispatch
    if tool_entry is None:
        return await _execute_builtin(tool_name, args, project_id, stage)

    # Risk gate (T2.3 / OD-06 / WP-C3): an L3+ tool must NOT execute silently. There is
    # a SINGLE action_approval Gate per (project_id, run_id, tool_name). execute_tool is
    # gate-aware so an approved action re-dispatches (executes for real) rather than being
    # parked behind an endless second gate (B-R17.2-TOOL-DOUBLEGATE). Honest degradation if
    # no Gate backend (see _create_risk_gate).
    risk = tool_entry.risk_level.value if hasattr(tool_entry.risk_level, "value") else "L0"
    meta = tool_entry.type_metadata or {}
    write_scope = meta.get("write_scope", "none")
    approved_gate_id = ""  # set below iff a re-dispatched (gate-approved) call executes

    if _rank(risk) >= _rank(_GATE_RISK_THRESHOLD):
        gate_state, gate_id = _resolve_action_gate(project_id, run_id, tool_name)
        approved = confirmed or gate_state == "approved"
        if not approved:
            if gate_state == "pending" and gate_id:
                result = {
                    "status": "awaiting_approval",
                    "risk_level": risk,
                    "tool_name": tool_name,
                    "gate_id": gate_id,
                    "gate_type": "action_approval",
                    "message": (f"工具风险等级 {risk} ≥ {_GATE_RISK_THRESHOLD}，已有等待中的 "
                                f"action_approval Gate（{gate_id}），审批通过后方可执行。"),
                }
            else:
                result = _create_risk_gate(project_id, run_id, stage, tool_name, risk)
            _write_trace(tracer, project_id, tool_name, args, result)
            return result
        # approved → fall through to real execution (re-dispatch). Remember the gate id
        # so a successful high-risk write/execute consumes it (one-time approval, R18→R17.2).
        # confirmed=True is a direct-thread with no gate_id → nothing to consume.
        if not confirmed and gate_state == "approved":
            approved_gate_id = gate_id

    # PreToolUse hooks (WP-4 / GAP-SEC-2): run the Registry-registered PreToolUse hooks
    # before the real dispatch. A block-mode hook returning "block" (e.g. pre-write policy
    # rejecting a source/ write or secret-bearing content) stops execution — the tool does
    # NOT run. Fully data-driven from the Registry (no hardcoded tool list). Read-only/MCP
    # tools are passed through by the hook impl itself.
    try:
        from app.services.hook_engine import run_hooks
        _svc_wr = None
        try:
            from app.dependencies import get_services
            _svc_wr = get_services()
        except Exception:
            _svc_wr = None
        pre = run_hooks(
            "PreToolUse",
            {"project_id": project_id, "tool_name": tool_name,
             "write_scope": write_scope, "args": args, "stage": stage},
            db,
            tracer=tracer or (getattr(_svc_wr, "trace_writer", None) if _svc_wr else None),
            auditor=(getattr(_svc_wr, "audit_writer", None) if _svc_wr else None),
        )
        if pre.blocked:
            result = {
                "status": "blocked_by_hook",
                "tool_name": tool_name,
                "hook_point": "PreToolUse",
                "reason": pre.block_reason,
                "hooks_run": [r.hook_name for r in pre.results],
            }
            _write_trace(tracer, project_id, tool_name, args, result)
            return result
    except Exception:
        # 发声：Hook 引擎异常必须可见，但不因引擎故障阻断已授权的合法工具（fail-open 仅限引擎自身故障）。
        logger.warning("tool_registry: PreToolUse hook 引擎异常 tool=%s", tool_name, exc_info=True)

    # Execute by scope. run_safe_command / execute-scope tools are checked first because
    # run_safe_command carries no write_scope (defaults to "none") yet must run via provider.
    if tool_name == "run_safe_command" or write_scope in ("execute", "system"):
        result = await _execute_via_provider(tool_name, args, project_id, tool_entry)
    elif write_scope in ("none",):
        result = await _execute_read(tool_name, args, project_id)
    elif write_scope in ("workspace",):
        result = await _execute_workspace_write(tool_name, args, project_id, tool_entry)
    elif write_scope in ("patch_draft",):
        result = await _execute_generate_patch(tool_name, args, project_id, tool_entry)
    elif write_scope in ("output_code",):
        result = await _execute_apply_patch(tool_name, args, project_id, tool_entry)
    elif args.get("command") or args.get("code"):
        # A gate-approved exec tool without an explicit write scope → run via provider.
        result = await _execute_via_provider(tool_name, args, project_id, tool_entry)
    else:
        result = await _execute_builtin(tool_name, args, project_id, stage)

    # One-time consumption of an approved action_approval Gate (R18→R17.2). Only after a
    # gate-approved re-dispatch of a high-risk write/execute tool actually SUCCEEDS do we
    # mark the gate consumed — so the same approval can never be reused for a second
    # high-risk write. Failures (error/rejected/awaiting_approval) leave the gate approved
    # so the caller can retry without re-approval. confirmed=True (no gate_id) is skipped.
    if (
        approved_gate_id
        and write_scope in ("output_code", "execute", "system")
        and not result.get("error")
        and result.get("status") not in ("error", "rejected", "awaiting_approval")
    ):
        try:
            from app.dependencies import get_services
            get_services().gate_service.mark_consumed(approved_gate_id)
        except Exception as e:
            # 发声：消费失败若静默，该审批会被误当作可重复使用。
            logger.warning("tool_registry: gate 一次性消费失败 gate=%s tool=%s: %s",
                           approved_gate_id, tool_name, e)

    # PostToolUse hooks (WP-4 / GAP-SEC-2): advisory pass after a successful dispatch.
    try:
        from app.services.hook_engine import run_hooks
        run_hooks(
            "PostToolUse",
            {"project_id": project_id, "tool_name": tool_name,
             "write_scope": write_scope, "args": args, "stage": stage,
             "result_status": result.get("status")},
            db, tracer=tracer,
        )
    except Exception:
        logger.warning("tool_registry: PostToolUse hook 引擎异常 tool=%s", tool_name, exc_info=True)

    _write_trace(tracer, project_id, tool_name, args, result)
    return result

async def _execute_builtin(tool_name: str, args: dict, project_id: str, stage: str) -> dict:
    """Handle the three built-in tool implementations."""
    if tool_name == "get_project_info":
        from app.services import workspace_service
        ws = workspace_service.workspace_path(project_id)
        art_dir = ws / "artifacts"
        # D-107: 产物按 artifacts/{stage}/ 分层；递归各阶段子目录收集，过滤 `_` 清单文件。
        artifacts: list = []
        if art_dir.exists():
            for p in sorted(art_dir.iterdir()):
                if p.is_file() and not p.name.startswith("_"):
                    artifacts.append(p.name)
            for st in ("p0", "p1", "p2", "p3", "p4", "p5", "p6"):
                sub = art_dir / st
                if sub.is_dir():
                    for p in sorted(sub.iterdir()):
                        if p.is_file() and not p.name.startswith("_"):
                            artifacts.append(f"{st}/{p.name}")
        return {"project_id": project_id, "current_stage": stage,
                "artifacts": artifacts, "source": "tool_registry_builtin"}
    elif tool_name == "read_artifact":
        path = args.get("artifact_path", "")
        try:
            from app.services.workspace_service import workspace_path
            art_root = workspace_path(project_id) / "artifacts"
            full = art_root / path
            # D-107: 传入已含 {stage}/ 前缀时按原样解析；若传入裸文件名且扁平根找不到，
            # 回退在 artifacts/{stage}/ 各子目录查找同名文件（找不到诚实返回不存在）。
            if not full.exists() and "/" not in path:
                for st in ("p0", "p1", "p2", "p3", "p4", "p5", "p6"):
                    cand = art_root / st / path
                    if cand.exists():
                        full = cand
                        break
            if full.exists():
                # 批2: raise the 4000-char cap so agents can read large upstream artifacts
                # (e.g. 25KB p3_task_plans.json) fully; still bounded to avoid unbounded blobs.
                return {"artifact_path": path,
                        "content": full.read_text("utf-8", errors="replace")[:200_000]}
            return {"artifact_path": path, "error": "文件不存在"}
        except Exception as e:
            return {"artifact_path": path, "error": str(e)}
    elif tool_name == "run_profiling":
        if stage not in ("p1", "p2", "p3", "p4", "p5", "p6"):
            return {"error": f"P1 全量识别仅在 P1 及之后阶段可用，当前: {stage}"}
        try:
            from app.dependencies import get_services
            from app.services.full_stack_profiler import FullStackProfiler
            svc = get_services()
            profiler = FullStackProfiler(trace_writer=svc.trace_writer, audit_writer=svc.audit_writer)
            result = profiler.profile(project_id)
            return {"status": "completed", "items_completed": result.get("items_completed", 0)}
        except Exception as e:
            return {"error": f"全量识别执行失败: {e}"}
    elif tool_name == "p5_verification_facts":
        # R17.5-P5-R1: 暴露 P5 确定性验证的真实事实（读已落盘报告，只读 L0）。供 P5 策略/解读
        # agent 在工具循环中按需取 ground-truth；不得改写、不由此翻转 can_be_completed。
        try:
            from app.services.workspace_service import workspace_path
            fp = workspace_path(project_id) / "artifacts" / "p5_validation_report.json"
            if not fp.exists():
                return {"available": False,
                        "error": "P5 验证报告尚未生成（确定性验证未运行或本项目无 P5 产物）"}
            import json as _json
            report = _json.loads(fp.read_text("utf-8", errors="replace"))
            plan = report.get("validation_plan", {}) or {}
            return {
                "available": True,
                "source": "artifacts/p5_validation_report.json (deterministic ground truth)",
                "can_be_completed": report.get("can_be_completed", False),
                "slots": plan.get("slots", []),
                "verify_results_failed": report.get("verify_results", []),
                "conditional_results": report.get("conditional_results", []),
                "note": ("以上为确定性验证器铁证，只可解读/规划，不可改写；"
                         "completed 由 can_mark_completed 认定，不由本工具或模型翻转"),
            }
        except Exception as e:
            return {"available": False, "error": str(e)}
    elif tool_name == "p5_dimension_capabilities":
        # R17.5-P5-R2 (GAP-P5-1/3, capability-first): 验证维度能力清单 + 环境探测（只读 L0）。
        # 非门禁事实——不参与 can_be_completed；维度适用性由 P5 skill/LLM 判断。
        try:
            from app.services.p5_capability_service import P5CapabilityService
            return P5CapabilityService().probe_all(project_id)
        except Exception as e:
            logger.warning("p5_dimension_capabilities failed (non-blocking): %s", e, exc_info=True)
            return {"error": str(e), "dimensions": []}
    elif tool_name == "p5_verify_dimension":
        # R17.5-P5-R2: 探测单个维度能力+环境（确定性事实，不产"通过"结论、不翻转门禁）。
        try:
            from app.services.p5_capability_service import P5CapabilityService
            dim = (args or {}).get("dimension", "")
            return P5CapabilityService().verify_dimension(project_id, dim).to_dict()
        except Exception as e:
            logger.warning("p5_verify_dimension failed (non-blocking): %s", e, exc_info=True)
            return {"error": str(e)}
    if tool_name == "introduce_community_resource":
        from app.services.community_introduction import introduce
        from app.core.database import get_session
        if not project_id:
            # 公理3：缺失真实 project 上下文时发声，不静默归到伪项目。
            logger.warning("introduce_community_resource called without project_id; refusing to attribute to a fake project")
            return {"error": "缺少 project_id：社区资源引入需真实项目上下文，拒绝执行以避免 Gate/审计错归属"}
        query = (args or {}).get("query")
        rtype = (args or {}).get("resource_type")
        db = get_session()
        try:
            return introduce(db, query=query, type=rtype, project_id=project_id, stage=stage)
        finally:
            db.close()
    return {"error": f"未知内置工具: {tool_name}"}


async def _execute_mcp_tool(tool_name: str, args: dict, db: Optional[Session]) -> dict:
    """Dispatch an mcp__server__tool call."""
    # tool_name format: mcp__{server_slug}__{tool}
    parts = tool_name.split("__", 2)
    if len(parts) < 3:
        return {"error": f"无法解析 MCP 工具名: {tool_name}"}
    _prefix, server_slug, mcp_tool_name = parts
    if db is None:
        return {"error": "DB not available for MCP dispatch"}
    try:
        from app.services.mcp_service import MCPService
        svc = MCPService(db)
        servers, _total = svc.list_all()
        for srv in servers:
            slug = srv.name.lower().replace("-", "_")
            if slug == server_slug and srv.status == "connected":
                result = await svc.call_tool(srv.mcp_id, mcp_tool_name, args)
                return result
        return {"error": f"MCP server '{server_slug}' 未找到或未连接"}
    except Exception as e:
        return {"error": f"MCP dispatch failed: {e}"}


async def _execute_read(tool_name: str, args: dict, project_id: str) -> dict:
    """Local read-only tool execution within workspace (write_scope=none).

    B-TOOL-SCHEMA-1 (R11-3): dispatch by tool so the L1 read tools are genuinely
    usable with their real params — fs_read reads a file, list_files lists a dir,
    code_grep searches. All confined to the project workspace.
    """
    from app.services.workspace_service import workspace_path
    ws = workspace_path(project_id)
    ws_root = ws.resolve()

    def _confine(rel: str):
        full = (ws / rel).resolve()
        if not str(full).startswith(str(ws_root)):
            return None
        return full

    # ── list_files: list a directory ──
    if tool_name == "list_files":
        rel = args.get("path") or args.get("dir") or "source"
        full = _confine(rel)
        if full is None:
            return {"error": "路径越界: 仅允许访问项目 workspace 内目录"}
        if not full.exists() or not full.is_dir():
            return {"error": f"目录不存在: {rel}"}
        entries = []
        for p in sorted(full.iterdir())[:500]:
            entries.append({"name": p.name, "type": "dir" if p.is_dir() else "file"})
        return {"path": rel, "count": len(entries), "entries": entries}

    # ── code_grep: search a pattern under a directory ──
    if tool_name == "code_grep":
        import re
        pattern = args.get("pattern") or args.get("query", "")
        if not pattern:
            return {"error": "需要 pattern 参数"}
        rel = args.get("path") or "source"
        base = _confine(rel)
        if base is None:
            return {"error": "路径越界: 仅允许检索 workspace 内目录"}
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return {"error": f"无效正则: {e}"}
        skip = {".git", "node_modules", "__pycache__", ".venv"}
        matches = []
        scanned = 0
        for p in base.rglob("*"):
            if not p.is_file() or any(s in p.parts for s in skip):
                continue
            scanned += 1
            if scanned > 5000:
                break
            try:
                for i, line in enumerate(p.read_text("utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        matches.append({"file": str(p.relative_to(ws_root)), "line": i, "text": line.strip()[:200]})
                        if len(matches) >= 100:
                            break
            except Exception:
                continue
            if len(matches) >= 100:
                break
        return {"pattern": pattern, "path": rel, "match_count": len(matches),
                "matches": matches, "truncated": len(matches) >= 100}

    # ── fs_read (default read-only): read a file's content ──
    target = args.get("path") or args.get("file") or args.get("artifact_path", "")
    if not target:
        return {"error": "需要 path/file/artifact_path 参数"}
    full = _confine(target)
    if full is None:
        return {"error": "路径越界: 仅允许访问项目 workspace 内文件"}
    if full.exists() and full.is_file():
        # 批2: fs_read previously hard-truncated to 4000 chars — far too small for the
        # P0-P4 agents to read a real source file (e.g. a 39KB .cs) for migration, so the
        # model saw a chopped file, narrated "the file is truncated, let me try…" and never
        # produced migrated code. Raise the window and add offset paging so large files can
        # be read fully (in chunks when needed); report total/truncation honestly.
        text = full.read_text("utf-8", errors="replace")
        try:
            offset = max(0, int(args.get("offset", 0) or 0))
            limit = int(args.get("limit", 50000) or 50000)
        except (TypeError, ValueError):
            offset, limit = 0, 50000
        limit = max(1, min(limit, 200_000))
        chunk = text[offset:offset + limit]
        return {"content": chunk, "path": target, "total_chars": len(text),
                "offset": offset, "returned_chars": len(chunk),
                "truncated": (offset + limit) < len(text)}
    return {"error": f"文件不存在: {target}"}


async def _execute_workspace_write(tool_name: str, args: dict, project_id: str, entry) -> dict:
    """Workspace-confined write (write_scope=workspace, e.g. fs_write_artifact).

    WP-C1 (B-R17.2-TOOL-NODISPATCH): real write through WorkspaceMediator — the single
    write gatekeeper (D-099 / D-104). source/ is rejected unconditionally; new code lands
    in output_code/, reports in artifacts/, patch drafts in patches/. A bare filename or an
    unrecognized top-level dir is canonicalized into output_code/ (the new-code write area).
    Never returns a stub.
    """
    rel = (args.get("path") or args.get("file") or "").strip()
    content = args.get("content")
    if not rel:
        return {"error": "fs_write_artifact 需要 path 参数（相对 workspace，建议置于 output_code/ 或 artifacts/）"}
    if content is None:
        return {"error": "fs_write_artifact 需要 content 参数（要写入的文件内容）"}

    first = rel.replace("\\", "/").split("/", 1)[0]
    if first not in ("output_code", "artifacts", "patches", "source"):
        if _STAGE_DIR_RE.match(first):
            # R19-3-01：`p0/…` `p1/…` 是阶段产物（报告/清单 json），不是迁移代码。
            # 旧版一律前缀 output_code/，真跑后 output_code 顶层混入 p0/ 与 p1/ 的 json。
            # 按阶段目录形态归到 artifacts/，output_code/ 只留代码。
            rel = f"artifacts/{rel}"
        else:
            # Canonicalize bare/unknown paths into the new-code write area (source stays source/
            # so the D-099① read-only rejection below still fires).
            rel = f"output_code/{rel}"

    # D-114 文件名清洗（治"中文描述污染文件名"）：模型常把描述写进 path（如
    # `output_code/x/MicroDBHelper.cs（QueryExcel 方法）` 或 `.../JS、Layui 面板）`），
    # 产出非法路径、不可编译。此处对每个路径段清洗：截断合法扩展名后的括注、剔除 CJK/空格/
    # 标点为 _，保留 ASCII 标识符+扩展名与目录分隔。顶层目录白名单段保持不变。
    rel = _sanitize_write_path(rel)

    try:
        from app.services.workspace_mediator import _workspace_mediator_for
        mediator = _workspace_mediator_for(project_id)
        target, write_risk = mediator.check_write(rel)
    except ValueError as e:
        # D-099① source/ or boundary rejection — surface it, do not silently pass (公理3).
        return {"status": "rejected", "tool_name": tool_name, "path": rel, "error": str(e)}
    except Exception as e:
        return {"error": f"workspace 写入解析失败: {e}"}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content if isinstance(content, str) else str(content)
        target.write_text(data, encoding="utf-8")
    except Exception as e:
        return {"error": f"workspace 写入失败: {e}"}

    from app.services.workspace_service import workspace_path
    ws_root = workspace_path(project_id).resolve()
    try:
        rel_out = str(target.resolve().relative_to(ws_root))
    except ValueError:
        rel_out = rel
    return {
        "status": "written",
        "tool_name": tool_name,
        "path": rel_out,
        "bytes": len(data.encode("utf-8")),
        "write_risk": write_risk,
    }


async def _execute_generate_patch(tool_name: str, args: dict, project_id: str, entry) -> dict:
    """Write a patch draft into patches/ (write_scope=patch_draft).

    WP-C2 (B-R17.2-TOOL-NODISPATCH): generate_patch previously fell through to
    _execute_builtin → "未知内置工具" (mis-routed). It now produces a real draft under
    patches/ via WorkspaceMediator (D-099③ / D-104 — platform-writable, user-read-only).
    The draft is a self-describing envelope so apply_patch_with_confirm can resolve the
    target + content later. It never touches source/ or output_code/.
    """
    target_path = (args.get("target_path") or args.get("path") or "").strip()
    diff = args.get("diff") or args.get("content") or ""
    if not target_path:
        return {"error": "generate_patch 需要 target_path 参数（补丁针对的目标文件，相对 workspace）"}
    if not diff:
        return {"error": "generate_patch 需要 diff 参数（unified diff 文本或期望的新内容）"}

    # Derive a stable, path-safe draft filename under patches/.
    import hashlib
    safe = target_path.replace("\\", "/").strip("/").replace("/", "__")
    digest = hashlib.sha256(f"{target_path}".encode("utf-8")).hexdigest()[:8]
    patch_rel = f"patches/{safe}.{digest}.patch"

    try:
        from app.services.workspace_mediator import _workspace_mediator_for
        mediator = _workspace_mediator_for(project_id)
        target, write_risk = mediator.check_write(patch_rel)
    except ValueError as e:
        return {"status": "rejected", "tool_name": tool_name, "path": patch_rel, "error": str(e)}
    except Exception as e:
        return {"error": f"patch 草案路径解析失败: {e}"}

    import json as _json
    envelope = {
        "kind": "patch_draft",
        "target_path": target_path,
        "diff": diff,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"error": f"patch 草案写入失败: {e}"}

    return {
        "status": "patch_drafted",
        "tool_name": tool_name,
        "patch_ref": patch_rel,
        "target_path": target_path,
        "write_risk": write_risk,
        "note": "补丁草案已写入 patches/（未落 source/ 或 output_code/）；需经 apply_patch_with_confirm + 用户 Gate 才应用到 output_code/。",
    }


async def _execute_apply_patch(tool_name: str, args: dict, project_id: str, entry) -> dict:
    """Apply a patch draft into output_code/ (write_scope=output_code, L4, gate-approved).

    WP-C3: reached only after the action_approval Gate is approved (re-dispatch). Reads the
    patches/ draft through the Mediator's read guard, then writes the resulting content into
    output_code/ through the Mediator's write check. source/ stays read-only (D-099①): a
    target resolving under source/ is rejected by the Mediator.
    """
    patch_ref = (args.get("patch_ref") or "").strip()
    target_path = (args.get("target_path") or "").strip()
    if not patch_ref:
        return {"error": "apply_patch_with_confirm 需要 patch_ref 参数（patches/ 下的补丁草案）"}

    try:
        from app.services.workspace_mediator import _workspace_mediator_for
        mediator = _workspace_mediator_for(project_id)
        draft_path = mediator.guard_read(patch_ref)
    except ValueError as e:
        return {"status": "rejected", "tool_name": tool_name, "error": str(e)}
    except Exception as e:
        return {"error": f"补丁草案读取解析失败: {e}"}
    if not draft_path.exists() or not draft_path.is_file():
        return {"error": f"补丁草案不存在: {patch_ref}"}

    import json as _json
    raw = draft_path.read_text("utf-8", errors="replace")
    try:
        env = _json.loads(raw)
        drafted_target = env.get("target_path") or ""
        new_content = env.get("diff") or ""
    except Exception:
        # Draft is not our envelope — treat the raw draft as the new content.
        drafted_target = ""
        new_content = raw

    # Resolve the output target: explicit arg > draft's target_path; canonicalize into
    # output_code/ (never source/ — that is rejected by the Mediator below).
    out_rel = target_path or drafted_target
    if not out_rel:
        return {"error": "无法确定应用目标：请提供 target_path 或在补丁草案中记录 target_path"}
    first = out_rel.replace("\\", "/").split("/", 1)[0]
    if first not in ("output_code", "artifacts", "patches", "source"):
        out_rel = f"output_code/{out_rel}"

    try:
        out_target, write_risk = mediator.check_write(out_rel)
    except ValueError as e:
        # D-099①: e.g. applying into source/ is rejected unconditionally.
        return {"status": "rejected", "tool_name": tool_name, "path": out_rel, "error": str(e)}
    except Exception as e:
        return {"error": f"应用目标解析失败: {e}"}

    # Decide apply mode from the drafted content. A unified diff is applied hunk by
    # hunk against a real base; anything else is treated as whole-file new content
    # (backward compatible with generate_patch drafts that store full content).
    if _looks_like_unified_diff(new_content):
        apply_mode = "unified_diff"
        # Base content: existing output_code/ target first; else the source/ original
        # (read-only, D-099① — via the Mediator read guard); else empty.
        base_text = None
        if out_target.exists() and out_target.is_file():
            base_text = out_target.read_text("utf-8", errors="replace")
        else:
            src_rel = _source_equivalent(out_rel)
            if src_rel:
                try:
                    src_path = mediator.guard_read(src_rel)
                    if src_path.exists() and src_path.is_file():
                        base_text = src_path.read_text("utf-8", errors="replace")
                except ValueError:
                    # source/ read boundary — leave base empty (no source original).
                    base_text = None
        if base_text is None:
            base_text = ""
        try:
            final_content = apply_unified_diff(base_text, new_content)
        except ValueError as e:
            # D-097: context mismatch / unappliable hunk → reject WITHOUT writing, so the
            # existing target file is never corrupted.
            return {
                "status": "rejected",
                "tool_name": tool_name,
                "patch_ref": patch_ref,
                "path": out_rel,
                "apply_mode": "unified_diff",
                "error": f"unified diff 应用失败（上下文不匹配/hunk 无法定位），目标文件未改动: {e}",
            }
        note = ("unified diff 已按 hunk 应用到 output_code/ 目标（base=现有 output_code/ 目标或"
                "source/ 只读原文，都无则空串；source/ 始终只读，D-099①）。")
    else:
        apply_mode = "full_content"
        final_content = new_content
        note = "补丁草案为整文件内容，已整体写入 output_code/（source/ 始终只读，D-099①）。"

    try:
        out_target.parent.mkdir(parents=True, exist_ok=True)
        out_target.write_text(final_content, encoding="utf-8")
    except Exception as e:
        return {"error": f"补丁应用失败: {e}"}

    from app.services.workspace_service import workspace_path
    ws_root = workspace_path(project_id).resolve()
    try:
        rel_out = str(out_target.resolve().relative_to(ws_root))
    except ValueError:
        rel_out = out_rel
    return {
        "status": "patch_applied",
        "tool_name": tool_name,
        "patch_ref": patch_ref,
        "path": rel_out,
        "bytes": len(final_content.encode("utf-8")),
        "apply_mode": apply_mode,
        "write_risk": write_risk,
        "note": note,
    }


async def _execute_via_provider(tool_name: str, args: dict, project_id: str, entry) -> dict:
    """Route through ExecutionProvider for execute-scope tools (e.g. run_safe_command).

    WP-C4: fixed to construct the provider via get_execution_provider() (honors
    EXECUTION_MODE). The previous svc.execution_provider attribute never existed on Services
    → the call always raised AttributeError, so this path never actually executed. The
    provider still enforces DENY_SUBSTRINGS (L5 hard block) and, in the default "local"
    mode, the ALLOWED_COMMANDS whitelist appropriate for "run_safe_command".
    """
    code = args.get("code") or args.get("command", "")
    if not code:
        return {"error": "execute 类工具需要 code 或 command 参数"}
    try:
        from app.services.execution_provider import get_execution_provider
        from app.services.workspace_service import workspace_path
        ws = str(workspace_path(project_id))
        provider = get_execution_provider()
        result = await provider.execute(code, language="bash", timeout=30, cwd=ws)
        return result
    except Exception as e:
        return {"error": f"ExecutionProvider failed: {e}"}


def _resolve_action_gate(project_id: str, run_id: str, tool_name: str) -> tuple[str, str]:
    """Resolve the single action_approval Gate for (project_id, run_id, tool_name).

    WP-C3 / B-R17.2-TOOL-DOUBLEGATE: returns ("approved", gate_id) when an approved gate
    for this action exists (either one execute_tool created, or one the agent_loop
    authorization layer created — both put the tool name in the gate reason/summary), so a
    re-dispatched call executes instead of opening a SECOND gate. Returns ("pending",
    gate_id) when a waiting gate exists (reuse it, no duplicate). Returns ("none", "")
    otherwise. Never fabricates a gate.
    """
    try:
        from app.dependencies import get_services
        gs = get_services().gate_service
        gates = gs.list_by_project(project_id)
    except Exception as e:
        logger.warning("_resolve_action_gate: gate lookup failed for %s: %s", tool_name, e)
        return ("none", "")

    def _matches(g) -> bool:
        if g.gate_type != "action_approval":
            return False
        if run_id and (g.run_id or "") != run_id:
            return False
        blob = f"{g.reason or ''} {g.summary or ''}"
        return tool_name in blob

    approved = [g for g in gates if _matches(g) and g.gate_status == "approved"]
    if approved:
        return ("approved", approved[-1].gate_id)
    pending = [g for g in gates if _matches(g) and g.gate_status == "waiting_decision"]
    if pending:
        return ("pending", pending[-1].gate_id)
    return ("none", "")



def _create_risk_gate(project_id: str, run_id: str, stage: str,
                      tool_name: str, risk: str) -> dict:
    """OD-06: an L3+ tool requires human approval before it runs. Create a real
    action_approval Gate through the existing GateService kernel (DB-persisted +
    audited — the same kernel as agent_loop._create_action_gate). action_approval
    Gates do NOT drive stage promotion (公理6).

    Honest degradation (红线：不得伪造 gate)：when no GateService is available in the
    current runtime context, return an explicit risk_flagged status explaining why —
    never fabricate a gate_id / awaiting_approval.
    """
    try:
        from app.dependencies import get_services
        gs = get_services().gate_service
    except Exception as e:
        return {
            "status": "risk_flagged",
            "risk_level": risk,
            "tool_name": tool_name,
            "message": (f"工具风险等级 {risk} ≥ {_GATE_RISK_THRESHOLD}，需人工审批；"
                        f"但当前运行上下文无可用 Gate 服务（{e}），未创建审批门。"),
        }
    try:
        gate = gs.create(
            project_id=project_id, run_id=run_id or "", stage=stage,
            gate_type="action_approval", risk_level=risk,
            reason=f"高风险工具 {tool_name}（风险 {risk}）执行前需人工审批",
            summary=f"Agent 拟执行高风险工具 {tool_name}（{risk}），请审批",
            options=["approve", "reject"],
        )
    except Exception as e:
        logger.warning("action_approval gate create failed for tool %s: %s", tool_name, e)
        return {
            "status": "risk_flagged",
            "risk_level": risk,
            "tool_name": tool_name,
            "message": (f"工具风险等级 {risk} ≥ {_GATE_RISK_THRESHOLD}，需人工审批；"
                        f"创建 action_approval Gate 失败（{e}）。"),
        }
    return {
        "status": "awaiting_approval",
        "risk_level": risk,
        "tool_name": tool_name,
        "gate_id": gate.gate_id,
        "gate_type": "action_approval",
        "message": (f"工具风险等级 {risk} ≥ {_GATE_RISK_THRESHOLD}，已创建 action_approval "
                    f"Gate（{gate.gate_id}）等待人工审批后方可执行。"),
    }


def _write_trace(tracer, project_id: str, tool_name: str, args: dict, result: dict) -> None:
    if tracer is None:
        return
    try:
        summary = result.get("status") or result.get("error") or "ok"
        tracer.write(
            project_id=project_id,
            trace_type="tool_call",
            summary=f"{tool_name}: {str(summary)[:100]}",
            detail={"tool_name": tool_name, "args_keys": list(args.keys()), "result_keys": list(result.keys())},
        )
    except Exception:
        # 发声：工具调用 trace 落库失败会让该次调用在可观测链路中缺失，须可见。
        logger.warning("tool_registry: 写入 tool_call trace 失败 tool=%s", tool_name, exc_info=True)
