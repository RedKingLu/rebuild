"""Hook execution engine (R17.3-6 WP-4 / GAP-SEC-2).

D-030 defines security as three layers: Hook + Policy + Security/Authorization Agent.
R17.3-5 EG-1 confirmed the Hook layer had NO execution engine: hook resources were only
seed-registered (seed.py `pre-write Policy check`, hook_point=PreToolUse, hook_mode=block)
but nothing ever ran them — `grep run_hooks/execute_hook/PreToolUse/PostToolUse` in
services/graph returned empty.

This module is the real engine. `run_hooks(hook_point, ctx, db)` loads the enabled+active
hook resources whose `type_metadata.hook_point` matches, dispatches each to its built-in
implementation (resolved by `type_metadata.hook_impl` when present, else deterministically
by the resource's stable `name` — see `_resolve_impl` / B-R18-3), and aggregates the outcome:

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
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.services.security_authorization import contains_secret

logger = logging.getLogger("rebuild.hook_engine")

# Secret detection for the pre-write policy hook (D-032) is NOT maintained here as a
# second, independently-drifting pattern list. It used to be (a 3-pattern private copy
# that predated the URL-embedded-credential pattern `security_authorization` picked up
# on 2026-09-06/07 — `postgresql://user:pass@host` / `redis://:pass@host` sailed straight
# through this hook's old copy while `security_authorization.redact_secrets()` already
# caught it, B-R20-REDACT-THREE-IMPLS). `_impl_pre_write_policy` below calls
# `security_authorization.contains_secret()` — the single shared implementation — instead.


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
    if isinstance(content, str) and content and contains_secret(content):
        return "block", ("pre-write 策略拦截：拟写入内容包含疑似密钥/凭据"
                         "（sk-/AKIA/api_key/URL 内嵌凭据等），禁止将明文密钥写入工作区产物（D-032）")
    return "allow", "pre-write 策略校验通过"


def enforce_pre_write_policy(ctx: dict) -> tuple[str, str]:
    """Code-layer, Registry-independent entry point for the D-032/D-099① pre-write check.

    `run_hooks()` below only dispatches hook resources whose `ResourceEntry.enabled` is
    True — i.e. the check is only as strong as the DB row backing it. This function calls
    the exact same check body (`_impl_pre_write_policy`) but bypasses the Registry lookup
    entirely: it is meant to be called unconditionally by `tool_registry.execute_tool`
    (B-R21-HOOK-DISABLE-BYPASS) so that even if the `"pre-write Policy check"`
    `ResourceEntry` row is disabled — via `PATCH /resources/{id}/disable`, which today
    carries no risk-gating of its own for most resources — the underlying secret/
    source-readonly check still runs and still fails closed. Delegating to
    `_impl_pre_write_policy` (rather than re-implementing the check) guarantees the two
    call sites can never behaviorally drift apart.
    """
    return _impl_pre_write_policy(ctx)


_BUILTIN_HOOKS = {
    "pre_write_policy": _impl_pre_write_policy,
}

# ── Deterministic implementation binding (B-R18-3) ─────────────────────────────
# Binding must NOT depend solely on `type_metadata.hook_impl`: the real DB's
# `pre-write Policy check` row was written by an early seed version that predates that key
# (`{"hook_point": "PreToolUse", "hook_mode": "block"}`), and `seed_all` never backfills
# EXISTING rows (it only inserts when the resource table is empty). So the key was present
# in every tmp/test DB (tests green) while absent in the real DB — where `hook_impl` lookup
# returned None and the hook was silently skipped, leaving the D-032 secret-write
# interception completely inert. Same class as R11-3 / R14-5 「tests 绿 ≠ 真实库正常」.
#
# The stable identity is `name`: seed.py and seed_all() treat `name` as the resource identity
# everywhere (dedupe / incremental re-seed), whereas `resource_id` is a fresh uuid per seed
# run and is therefore not comparable across DBs.
_HOOK_NAME_BINDINGS = {
    "pre-write policy check": "pre_write_policy",
}

# Built-in implementations that carry a security red line (D-032 plaintext-secret write /
# D-099① source read-only). For these the two fail-OPEN defaults are tightened:
#   - missing `hook_mode` ⇒ "block" (instead of "warn");
#   - unresolvable implementation ⇒ fail-CLOSED (see run_hooks).
_SECURITY_CRITICAL_IMPLS = {"pre_write_policy"}


def _resolve_impl(hook_name: str, meta: dict):
    """Resolve a hook resource to its built-in implementation body.

    Returns ``(impl_key, impl_callable | None)``.

    Order: an explicit, KNOWN ``type_metadata.hook_impl`` wins (backward compatible with the
    seed/tmp-DB rows and with operator-declared bindings); otherwise the resource's stable
    ``name`` is used. A declared-but-unknown key (typo, or an impl that no longer exists)
    also falls through to the name binding, so a metadata typo cannot disarm a known
    security hook.
    """
    declared = str(meta.get("hook_impl") or "").strip()
    if declared and declared in _BUILTIN_HOOKS:
        return declared, _BUILTIN_HOOKS[declared]
    bound = _HOOK_NAME_BINDINGS.get((hook_name or "").strip().lower(), "")
    if bound:
        return bound, _BUILTIN_HOOKS.get(bound)
    return declared, None


def _resolve_mode(meta: dict, impl_key: str) -> str:
    """Resolve the hook mode. Missing `hook_mode` defaults to "block" for security-critical
    implementations and to "warn" for everything else (the documented advisory semantics of
    non-security hooks is preserved — see module docstring)."""
    declared = str(meta.get("hook_mode") or "").strip().lower()
    if declared:
        return declared
    return "block" if impl_key in _SECURITY_CRITICAL_IMPLS else "warn"


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
        impl_key, impl = _resolve_impl(r.name, meta)
        mode = _resolve_mode(meta, impl_key)
        if impl is None:
            # 公理3「异常必发声」：旧实现在此 logger.debug + continue 静默跳过，真实库上
            # D-032 写入期拦截因此整条失守而无人察觉（B-R18-3）。现在改为发声 + 安全类
            # fail-closed。
            if mode == "block":
                reason = (f"hook fail-closed：block 模式 hook「{r.name}」无法绑定到内置实现体"
                          f"（hook_impl={impl_key or '未声明'}），按安全优先视为拦截，"
                          f"不放行本次工具调用。请修正该 hook 资源的 name 或 "
                          f"type_metadata.hook_impl，或停用该 hook。")
                logger.error(
                    "hook_engine: hook %r 无法绑定内置实现体(hook_impl=%r)，block 模式 "
                    "fail-closed（拦截本次调用）", r.name, impl_key)
                outcome.results.append(HookResult(hook_name=r.name, hook_mode=mode,
                                                  action="block", reason=reason))
                outcome.blocked = True
                outcome.block_reason = reason
            else:
                logger.warning(
                    "hook_engine: hook %r 无法绑定内置实现体(hook_impl=%r)，warn 模式跳过"
                    "（非安全类，不阻断；但配置需修正）", r.name, impl_key)
                outcome.warnings.append(
                    f"{r.name}: hook 无内置实现体（hook_impl={impl_key or '未声明'}），已跳过")
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


# ── Startup self-check on the REAL DB (B-R18-3, WP-A pattern) ──────────────────

def check_security_hook_bindings(db: Session) -> dict:
    """Read-only: can every registered hook row actually bind to an implementation body?

    Mirrors the ``check_migration_drift`` / ``verify_migration_head_on_startup`` split
    (R17.2 WP-A): a pure, testable inspection here + a loud, non-fatal reporter below.

    Root cause it guards: the hook resource rows live in the PERSISTENT DB, while the
    implementation bodies live in code. `seed_all` only inserts resource rows when the
    resource table is EMPTY and never backfills existing rows, so a row written by an older
    seed can drift out of sync with the code forever — and tmp-DB tests (fresh seed every
    run) can never see it. This check runs against whatever DB the process actually uses.

    Returns ``{"checked_rows": int, "bound_security_impls": [...], "findings": [...]}``
    where each finding is ``{"level": "error"|"warning", "code", "hook_name", "message"}``.
    Levels: ``error`` = a registered block-mode hook cannot run at all (definite
    misconfiguration on any DB); ``warning`` = metadata drift already covered by the
    deterministic name binding, or a security hook absent (which a brand-new/test DB
    legitimately is).
    """
    from app.models.resource_entry import ResourceEntry, ResourceType, ResourceStatus

    rows = db.query(ResourceEntry).filter(
        ResourceEntry.resource_type == ResourceType.hook,
        ResourceEntry.enabled == True,  # noqa: E712
        ResourceEntry.status == ResourceStatus.active,
    ).all()

    findings: list = []
    bound_security: list = []
    checked = 0
    for r in rows:
        meta = r.type_metadata or {}
        if not (meta.get("hook_point") or ""):
            continue  # 引擎不会加载无 hook_point 的行，不在自检范围
        checked += 1
        impl_key, impl = _resolve_impl(r.name, meta)
        mode = _resolve_mode(meta, impl_key)
        if impl is None:
            findings.append({
                "level": "error" if mode == "block" else "warning",
                "code": "hook_unbindable_block" if mode == "block" else "hook_unbindable_warn",
                "hook_name": r.name,
                "message": (f"hook「{r.name}」（{mode} 模式）无法绑定内置实现体"
                            f"（hook_impl={impl_key or '未声明'}）"
                            + ("，运行期将 fail-closed 拦截所有该挂点调用"
                               if mode == "block" else "，运行期将被跳过")),
            })
            continue
        if impl_key in _SECURITY_CRITICAL_IMPLS:
            bound_security.append(impl_key)
        if not str(meta.get("hook_impl") or "").strip():
            findings.append({
                "level": "warning", "code": "hook_impl_metadata_missing", "hook_name": r.name,
                "message": (f"hook「{r.name}」的 type_metadata 缺 hook_impl 键（seed 漂移：seed "
                            f"不回填已存在行）。已按 name 确定性绑定到 {impl_key}，运行期正常；"
                            f"如需清理数据可回填该键，但绑定不再依赖它。"),
            })
        if not str(meta.get("hook_mode") or "").strip():
            findings.append({
                "level": "warning", "code": "hook_mode_metadata_missing", "hook_name": r.name,
                "message": (f"hook「{r.name}」的 type_metadata 缺 hook_mode 键，已按实现体类别"
                            f"缺省为 {mode}（安全类 ⇒ block，其余 ⇒ warn）。"),
            })

    for impl_key in sorted(_SECURITY_CRITICAL_IMPLS):
        if impl_key not in bound_security:
            findings.append({
                "level": "warning", "code": "security_hook_absent", "hook_name": "",
                "message": (f"未找到任何绑定到安全实现体 {impl_key} 的 enabled+active hook 资源行"
                            f"⇒ 该安全红线（D-032 明文密钥写入 / D-099① source 只读）的**写入期**"
                            f"拦截当前缺位。全新库/测试库属正常（RESOURCE_SEEDS 仅在资源表为空时"
                            f"插入）；持久化真实库出现此项须补种该 hook 资源。"),
            })

    return {"checked_rows": checked,
            "bound_security_impls": sorted(set(bound_security)),
            "findings": findings}


def verify_security_hooks_on_startup(db: Optional[Session]) -> None:
    """Startup self-check (B-R18-3): surface real-DB hook binding drift LOUDLY (公理3).

    Non-fatal by design, mirroring ``verify_migration_head_on_startup``: detection/alarm
    only — it does NOT rewrite hook rows and does NOT disable anything. But it is never
    silent: an unbindable block-mode hook is logged at ERROR, drift/absence at WARNING.
    """
    log = logging.getLogger("rebuild.hook_engine")
    if db is None:
        log.warning("hook 绑定启动自检未执行：未提供数据库会话（无法判断真实库 hook 是否可绑定）")
        return
    try:
        report = check_security_hook_bindings(db)
    except Exception:
        log.warning("hook 绑定启动自检执行失败（非致命）", exc_info=True)
        return
    for f in report["findings"]:
        if f["level"] == "error":
            log.error("hook 绑定自检[%s]：%s", f["code"], f["message"])
        else:
            log.warning("hook 绑定自检[%s]：%s", f["code"], f["message"])
    if not report["findings"]:
        log.info("hook 绑定自检通过：%d 条 hook 行全部可绑定，安全实现体已绑定 %s",
                 report["checked_rows"], report["bound_security_impls"])

