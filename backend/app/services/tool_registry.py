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

import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("rebuild.tool_registry")

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

    _write_trace(tracer, project_id, tool_name, args, result)
    return result


# ── Internal dispatch helpers ─────────────────────────────────────────────────

async def _execute_builtin(tool_name: str, args: dict, project_id: str, stage: str) -> dict:
    """Handle the three built-in tool implementations."""
    if tool_name == "get_project_info":
        from app.services import workspace_service
        ws = workspace_service.workspace_path(project_id)
        art_dir = ws / "artifacts"
        artifacts = [p.name for p in sorted(art_dir.iterdir()) if p.is_file()] if art_dir.exists() else []
        return {"project_id": project_id, "current_stage": stage,
                "artifacts": artifacts, "source": "tool_registry_builtin"}
    elif tool_name == "read_artifact":
        path = args.get("artifact_path", "")
        try:
            from app.services.workspace_service import workspace_path
            full = workspace_path(project_id) / "artifacts" / path
            if full.exists():
                return {"artifact_path": path, "content": full.read_text("utf-8", errors="replace")[:4000]}
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
        return {"content": full.read_text("utf-8", errors="replace")[:4000], "path": target}
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
        # Canonicalize bare/unknown paths into the new-code write area (source stays source/
        # so the D-099① read-only rejection below still fires).
        rel = f"output_code/{rel}"

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
