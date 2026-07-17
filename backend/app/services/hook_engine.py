"""Hook execution engine (R17.3-6 WP-4 / GAP-SEC-2).

D-030 defines security as three layers: Hook + Policy + Security/Authorization Agent.
R17.3-5 EG-1 confirmed the Hook layer had NO execution engine: hook resources were only
seed-registered (seed.py `pre-write Policy check`, hook_point=PreToolUse, hook_mode=block)
but nothing ever ran them — `grep run_hooks/execute_hook/PreToolUse/PostToolUse` in
services/graph returned empty.

This module is the real engine. `run_hooks(hook_point, ctx, db)` loads the enabled+active
hook resources whose `type_metadata.hook_point` matches, dispatches each to its built-in
implementation (`type_metadata.hook_impl`), and aggregates the outcome:

  - a "block"-mode hook returning `block` → the whole outcome is blocked (the caller must
    NOT run the tool);
  - a "warn"-mode hook returning `block`/`warn` → recorded as a warning (non-blocking);
  - "allow" → no effect.

It is wired into `tool_registry.execute_tool` around the real tool dispatch (PreToolUse
before, PostToolUse after). Fully data-driven from the Registry (no hardcoded MicroOA):
the hook list, point and mode come from the seed/Registry; only the built-in check bodies
live here, keyed by `hook_impl`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("rebuild.hook_engine")

# Secret patterns for the pre-write policy hook (mirror P6 desensitization / D-032).
_SECRET_PATTERNS = [
    re.compile(r"sk-[a-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']?([^\s\"']{8,})"),
]


@dataclass
class HookResult:
    """Outcome of a single hook implementation."""
    hook_name: str
    hook_mode: str          # "block" | "warn"
    action: str             # "allow" | "block" | "warn"
    reason: str = ""


@dataclass
class HookOutcome:
    """Aggregated outcome of running all hooks at a point."""
    hook_point: str
    blocked: bool = False
    block_reason: str = ""
    results: list = field(default_factory=list)   # list[HookResult]
    warnings: list = field(default_factory=list)  # list[str]

    def to_dict(self) -> dict:
        return {
            "hook_point": self.hook_point,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "hooks_run": [r.hook_name for r in self.results],
            "warnings": self.warnings,
        }


# ── Built-in hook implementations (keyed by type_metadata.hook_impl) ───────────

def _impl_pre_write_policy(ctx: dict) -> tuple[str, str]:
    """Pre-write / pre-exec Policy check (PreToolUse).

    Real, non-placeholder behavior:
      - a write targeting source/ → block (D-099① source is read-only);
      - content carrying a secret-looking value (sk-/AKIA/api_key=…) → block (D-032:
        secrets must never be written into a workspace artifact);
    Otherwise allow. Applies to write/execute-scope tools only (read tools pass).
    """
    write_scope = (ctx.get("write_scope") or "").lower()
    if write_scope in ("none", "mcp"):
        return "allow", "只读/MCP 工具，pre-write 策略不适用"

    args = ctx.get("args") or {}
    target = str(args.get("path") or args.get("file") or args.get("target_path") or "").replace("\\", "/")
    if target.split("/", 1)[0] == "source":
        return "block", f"pre-write 策略拦截：禁止写入 source/（只读，D-099①）: {target}"

    content = args.get("content")
    if isinstance(content, str) and content:
        for pat in _SECRET_PATTERNS:
            if pat.search(content):
                return "block", ("pre-write 策略拦截：拟写入内容包含疑似密钥/凭据"
                                 "（sk-/AKIA/api_key 等），禁止将明文密钥写入工作区产物（D-032）")
    return "allow", "pre-write 策略校验通过"


_BUILTIN_HOOKS = {
    "pre_write_policy": _impl_pre_write_policy,
}


def _load_hooks(hook_point: str, db: Optional[Session]) -> list:
    """Load enabled+active hook resources whose metadata.hook_point matches."""
    if db is None:
        return []
    try:
        from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus
        rows = db.query(ResourceEntry).filter(
            ResourceEntry.resource_type == ResourceType.hook,
            ResourceEntry.enabled == True,  # noqa: E712
            ResourceEntry.status == ResourceStatus.active,
        ).all()
    except Exception:
        logger.warning("hook_engine: 加载 hook 资源失败", exc_info=True)
        return []
    out = []
    for r in rows:
        meta = r.type_metadata or {}
        if (meta.get("hook_point") or "") == hook_point:
            out.append(r)
    return out


def run_hooks(hook_point: str, ctx: dict, db: Optional[Session],
              tracer=None, auditor=None) -> HookOutcome:
    """Run all registered hooks at `hook_point` (e.g. "PreToolUse" / "PostToolUse").

    ctx carries at least: project_id, tool_name, write_scope, args.
    Returns a HookOutcome; caller checks `.blocked`.
    """
    outcome = HookOutcome(hook_point=hook_point)
    hooks = _load_hooks(hook_point, db)
    for r in hooks:
        meta = r.type_metadata or {}
        mode = (meta.get("hook_mode") or "warn").lower()
        impl_key = meta.get("hook_impl") or ""
        impl = _BUILTIN_HOOKS.get(impl_key)
        if impl is None:
            # No built-in body registered — honest skip (not a silent pass of the tool,
            # just this hook has no runtime; surfaced in logs). Never fabricate a result.
            logger.debug("hook_engine: hook %r 无内置实现(hook_impl=%r)，跳过", r.name, impl_key)
            continue
        try:
            action, reason = impl(ctx)
        except Exception as e:
            logger.warning("hook_engine: hook %r 执行异常: %s", r.name, e, exc_info=True)
            # A block-mode hook that errors fails CLOSED (block); a warn hook records a warning.
            action, reason = ("block" if mode == "block" else "warn"), f"hook 执行异常: {e}"
        res = HookResult(hook_name=r.name, hook_mode=mode, action=action, reason=reason)
        outcome.results.append(res)
        if action == "block" and mode == "block":
            outcome.blocked = True
            outcome.block_reason = reason
        elif action in ("block", "warn"):
            outcome.warnings.append(f"{r.name}: {reason}")

    # Observability: trace + (if blocked) audit — never silent (公理3 / D-030).
    _record(outcome, ctx, tracer, auditor)
    return outcome


def _record(outcome: HookOutcome, ctx: dict, tracer, auditor) -> None:
    project_id = ctx.get("project_id")
    tool_name = ctx.get("tool_name", "")
    if tracer is not None and (outcome.blocked or outcome.warnings):
        try:
            tracer.write(
                trace_type="hook_event",
                project_id=project_id,
                action=f"{outcome.hook_point}:{tool_name}",
                summary=(f"hook {outcome.hook_point} tool={tool_name} "
                         f"blocked={outcome.blocked} warnings={len(outcome.warnings)}"),
            )
        except Exception:
            logger.warning("hook_engine: trace 写入失败", exc_info=True)
    if outcome.blocked and auditor is not None:
        try:
            auditor.write(
                audit_type="hook_block",
                risk_level="L3",
                action=f"{outcome.hook_point}:{tool_name}",
                decision="blocked",
                reason=outcome.block_reason[:300],
                project_id=project_id,
            )
        except Exception:
            logger.warning("hook_engine: audit 写入失败", exc_info=True)
