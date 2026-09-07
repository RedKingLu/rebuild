"""Security / Authorization final-interception layer (R17.3-6 WP-4 / GAP-SEC-1).

D-030 defines a three-layer security mechanism: Hook + Policy + Security/Authorization
Agent. D-031 makes Policy the hard floor: "Policy is a hard constraint whose priority is
higher than Agent judgment; the Security/Authorization Agent cannot approve an action the
Policy forbids."

R17.3-5 found that only the deterministic Policy layer (`mode_policy.authorize_action`) was
truly wired, with:
  - no consistent Audit on every allow/deny,
  - no user-readable risk explanation, and
  - no place for a future Security/Authorization Agent to plug in.

This module provides `SecurityAuthorizationService.authorize()` — the single **final
interception layer** that:
  1. calls the deterministic Policy (`authorize_action`) as the AUTHORITATIVE floor,
  2. optionally consults a Security/Authorization Agent reviewer (extension slot), whose
     advice can only make the decision MORE restrictive — NEVER approve a Policy-forbidden
     action (D-031 enforced in code, proven by test),
  3. emits a structured risk explanation (why intercepted / why allowed, which layer
     decided, whether an agent participated), and
  4. writes an Audit record for every decision (allow and deny), with secrets redacted
     (D-032).

The Security/Authorization Agent is NOT itself an LLM agent this round (Policy is
authoritative per the WP-4 charter). The `agent_reviewer` parameter is the reserved
extension slot: a callable `(ctx) -> {"advice": "...", "reason": "..."}` that a later round
can back with a real LLM agent. When present, its advice is clamped by the Policy floor.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from app.services.mode_policy import authorize_action, RISK_ORDER, HIGH_RISK_FLOOR, _rank

log = logging.getLogger("rebuild.security_authorization")

# Decision restrictiveness ordering (higher index = more restrictive). The final decision
# is the MOST restrictive of {policy decision, agent-advised decision} so an agent can only
# tighten, never loosen, the Policy floor (D-031).
_DECISION_RESTRICTIVENESS = ["auto_approved", "approved", "require_confirmation", "escalate", "deny"]

# Secret-ish patterns to redact from any reason/explanation string before it is persisted
# to an Audit or returned to a caller (D-032). Mirrors the P6 desensitization patterns.
_SECRET_PATTERNS = [
    re.compile(r"sk-[a-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']?([^\s\"']{8,})"),
    # URL 内嵌凭据 scheme://user:pass@host（2026-09-06 补，B-R20-GATE-NO-PAYLOAD 施工时实测发现）
    #
    # 发现过程：为把工具入参写进 action_approval Gate（用户批准的修法），主窗口先对
    # redact_secrets 做对抗性用例验证，实测 `--password=xxx` 被正确脱敏、但
    # `postgresql://user:Pa55w0rd@host:5432/db` **完全漏过** —— 上面三条模式都不覆盖
    # URL 内嵌凭据形态。
    #
    # 这是**既有缺口而非本次引入**，且与已登记的 `Q-R18-1-3`（`_strip_url_credentials`
    # 以 `^scheme://` 锚定、无 scheme 的值不被剥离）属同一家族：凭据在 URL 里的形态
    # 一直是本仓脱敏的薄弱面。
    #
    # 必须先补这条再落 Gate：否则"把命令行写进 Gate + 审计"这个改动会把连接串里的
    # 口令持久化进 p_gate 表与审计记录（违 AGENTS §8 / D-032）。**明知有洞不得先灌数据。**
    #
    # 只替换口令段、保留 scheme/user/host —— 审批者仍需看出"连的是哪个库、用哪个账号"
    # 才能做知情决策；把整个 URL 打掉会重新回到"看不见内容无法审批"的老问题。
    #
    # ── 用户名段为何是 `*` 而非 `+`（2026-09-07 补，B-R20-REDACT-URL-NOUSER）────────
    # 初版写作 `[^\s:/@]+`（一个或多个），补测试覆盖时实测发现 **空用户名形态完全漏过**：
    #     redis://:Pa55w0rd@cache.internal:6379/0   → 口令原样保留（旧三条模式也不覆盖）
    # 而这并非畸形边角：`scheme://:password@host` 是 Redis 连接串的**标准写法**（Redis 6
    # ACL 之前只有 AUTH 口令、没有用户名），`redis-py` / Celery broker URL / Django
    # CACHES 配置全用这个形态；`mongodb://` 与 `amqp://`（RabbitMQ）同形。
    # 于是该口令会经 tool_registry._redacted_action_payload 原样写进 action_approval
    # Gate 的 summary 与审计记录 —— 与本条模式当初要堵的 `postgresql://user:pass@host`
    # 是同一个洞的两种形态。量词改 `*` 即可覆盖，替换式 `\1:[REDACTED]@` 无需改动：
    # group 1 = `scheme://user`，user 为空时正好得 `redis://:[REDACTED]@`。
    #
    # ── 为何**不**把 userinfo 扩到匹配最后一个 `@` ────────────────────────────────
    # 曾评估过 `([a-z][a-z0-9+.\-]*://[^\s@]*?):([^\s@]*)@`（放开 `/`，贪到最后一个 `@`），
    # 实测该方向会**摧毁合法 URL**：
    #     https://example.com:8080/path@anchor  →  https://example.com:[REDACTED]@anchor
    # 端口 + 路径被判成口令。脱敏的误伤比漏检更难发现（审批者看到的内容被无声抹掉，
    # 且无人会去核对），故**宁可窄不可宽**：口令段固定排除 `/`，让 `:8080/path` 无法被
    # 当成口令。此约束由 test_r20_gate_payload_redaction.py 的防误伤用例锁死，
    # 重构本条模式前先看那组用例。
    #
    # ── 有意不覆盖的边界：口令含未编码 `/` ───────────────────────────────────────
    #     postgresql://user:p/w@host/db  → 不脱敏（已知，有意接受）
    # RFC 3986 要求 userinfo 里的 `/` 必须百分号编码（`p%2Fw`），该形态本身是畸形 URL；
    # 而要覆盖它就必须允许口令段含 `/`，那立刻退化成上面被否掉的方向、造成
    # `https://example.com:8080/path@anchor` 那类误伤。**这是权衡后的边界，不是遗漏。**
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]*):([^\s/@]+)@"),
]

# 上面最后一条用捕获组保留 scheme://user 与 @，故其替换需专用 repl（见 redact_secrets）
_URL_CRED_PATTERN = _SECRET_PATTERNS[-1]


def redact_secrets(text: str) -> str:
    """Replace any secret-looking substring with [REDACTED] (D-032, never leak Key/Token)."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        if pat is _URL_CRED_PATTERN:
            # 保留 scheme://user 与 @host，只打掉口令段 —— 审批者仍需看出"连哪个库、用哪个账号"
            out = pat.sub(r"\1:[REDACTED]@", out)
        else:
            out = pat.sub("[REDACTED]", out)
    return out


def contains_secret(text: str | None) -> bool:
    """True iff `text` contains any secret-looking substring (D-032).

    Single source of truth for "does this text carry a secret". `hook_engine`'s
    pre-write policy hook (`_impl_pre_write_policy`) used to keep its own private
    3-pattern copy of `_SECRET_PATTERNS` that predated the URL-embedded-credential
    pattern added above (2026-09-06/07) — so the two implementations silently drifted
    apart and the `block`-mode D-032 hook let `postgresql://user:pass@host` /
    `redis://:pass@host` style content through undetected (B-R20-REDACT-THREE-IMPLS).
    `hook_engine` now calls this function instead of maintaining a second pattern list,
    so the two call sites can never drift apart again.
    """
    if not text:
        return False
    return any(pat.search(text) for pat in _SECRET_PATTERNS)


def _restrictiveness(decision: str) -> int:
    try:
        return _DECISION_RESTRICTIVENESS.index(decision)
    except ValueError:
        # Unknown decision → treat as most restrictive (fail closed).
        return len(_DECISION_RESTRICTIVENESS)


class SecurityAuthorizationService:
    """Final interception layer combining Policy floor + (optional) Agent advice + Audit.

    Construct with the shared `services` container (for the AuditWriter). Callers that only
    want the decision logic (no audit) can pass `services=None`.
    """

    def __init__(self, services=None):
        self._svc = services

    def authorize(
        self,
        *,
        mode: str,
        risk_level: str,
        action: str = "",
        in_plan: bool = False,
        confirmed: bool = False,
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        agent_reviewer: Optional[Callable[[dict], dict]] = None,
        write_audit: bool = True,
    ) -> dict:
        """Authorize an action through the Policy floor + optional Agent advice.

        Returns a dict:
          {
            decision, reason, risk_level, mode, reviewer,
            policy_decision,            # the raw deterministic Policy decision (floor)
            agent_advice,               # the agent's advice, if any (else None)
            agent_overridden_by_policy, # True iff agent tried to loosen and was clamped
            risk_explanation: {...},    # user-readable structured explanation
            audit_ref,                  # audit_id if an Audit was written
          }
        decision ∈ {auto_approved, approved, require_confirmation, escalate, deny}.
        """
        # 1. Deterministic Policy — the AUTHORITATIVE floor (D-031).
        policy = authorize_action(mode, risk_level, action,
                                  in_plan=in_plan, confirmed=confirmed)
        policy_decision = policy["decision"]
        final_decision = policy_decision
        final_reason = policy["reason"]
        risk = policy["risk_level"]

        # 2. Optional Security/Authorization Agent advice (extension slot). Advice may only
        #    tighten the decision; an attempt to loosen is clamped by the Policy floor.
        agent_advice = None
        agent_overridden = False
        if agent_reviewer is not None:
            try:
                advice = agent_reviewer({
                    "mode": mode, "risk_level": risk, "action": action,
                    "in_plan": in_plan, "policy_decision": policy_decision,
                }) or {}
                agent_advice = {
                    "advice": (advice.get("advice") or "").strip(),
                    "reason": redact_secrets((advice.get("reason") or "").strip())[:300],
                }
                advised = agent_advice["advice"]
                if advised in _DECISION_RESTRICTIVENESS:
                    if _restrictiveness(advised) > _restrictiveness(policy_decision):
                        # Agent is MORE restrictive → honor it (agent adds caution).
                        final_decision = advised
                        final_reason = (f"Policy 判定 {policy_decision}；安全/授权 Agent 建议更严"
                                        f"（{advised}）：{agent_advice['reason']}")
                    elif _restrictiveness(advised) < _restrictiveness(policy_decision):
                        # Agent tried to LOOSEN → clamped by Policy floor (D-031).
                        agent_overridden = True
                        final_reason = (f"安全/授权 Agent 建议 {advised}，但 Policy 底线为 "
                                        f"{policy_decision}（D-031：Agent 不得批准 Policy 未放行项），"
                                        f"以 Policy 为准。")
            except Exception:
                # Agent advice is advisory; failure must never loosen the floor (fail closed
                # keeps Policy decision). Surface the failure in logs (公理3).
                log.warning("SecurityAuthorization: agent_reviewer 异常，忽略其建议，回落 Policy 底线",
                            exc_info=True)

        # 3. Risk explanation (user-readable, structured).
        is_high = _rank(risk) >= HIGH_RISK_FLOOR
        risk_explanation = {
            "action": action,
            "risk_level": risk,
            "mode": mode,
            "decided_by": "policy" if not (agent_advice and final_decision != policy_decision)
                          else "policy+agent",
            "final_decision": final_decision,
            "policy_decision": policy_decision,
            "is_high_risk": is_high,
            "why": redact_secrets(final_reason),
            "policy_is_floor": True,
            "agent_overridden_by_policy": agent_overridden,
        }

        # 4. Audit every decision (allow AND deny) — redacted (D-032).
        audit_ref = None
        if write_audit and self._svc is not None:
            try:
                audit = self._svc.audit_writer.write(
                    audit_type="security_authorization",
                    risk_level=risk,
                    action=action or "authorize",
                    decision=final_decision,
                    reason=redact_secrets(final_reason)[:300],
                    project_id=project_id, run_id=run_id, stage=stage,
                    policy_decision=policy_decision,
                    agent_participated=bool(agent_advice),
                    agent_overridden_by_policy=agent_overridden,
                )
                if isinstance(audit, dict):
                    audit_ref = audit.get("audit_id")
            except Exception:
                # 发声：安全授权审计写入失败必须可见（审计链完整性），但不因此放行/阻断变化。
                log.warning("SecurityAuthorization: 审计写入失败 action=%s", action, exc_info=True)

        return {
            "decision": final_decision,
            "reason": final_reason,
            "risk_level": risk,
            "mode": mode,
            "reviewer": "security_authorization",
            "policy_decision": policy_decision,
            "agent_advice": agent_advice,
            "agent_overridden_by_policy": agent_overridden,
            "risk_explanation": risk_explanation,
            "audit_ref": audit_ref,
        }
