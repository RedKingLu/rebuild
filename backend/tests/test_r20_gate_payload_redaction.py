"""B-R20-GATE-NO-PAYLOAD — action_approval Gate 入参脱敏的回归测试。

commit 8c271d1 落地了「工具入参经脱敏后写入 action_approval Gate summary」的 P1 修法，
但只动了 3 个源文件、0 个测试文件。本文件补齐该修法的测试覆盖，分三组：

  A. redact_secrets() 对 URL 内嵌凭据（scheme://user:pass@host）的脱敏 —— 新补模式的直接覆盖，
     含「保留 scheme/user/host」的设计意图断言与「无凭据 URL 不得误伤」的防误伤断言。
  B. tool_registry._redacted_action_payload() 的行为 —— 空入参、正常入参、含凭据入参、
     截断，以及脱敏组件不可用时的 fail-closed（该函数的安全红线）。
  C. Gate 层端到端 —— 入参确实进了 Gate 且是脱敏后的形态；正常路径与
     「无 GateService 的诚实降级」路径都覆盖。

所有出现在断言里的凭据都是显式假值（Pa55w0rd / SECRETVALUE12345 之类），断言只检查
「假值不出现在输出里」。
"""

import re

import pytest


# 测试用假凭据（非真实凭据；仅用于断言「不出现在脱敏输出里」）
FAKE_DB_PASSWORD = "Pa55w0rd"
FAKE_API_KEY_VALUE = "SECRETVALUE12345"
FAKE_SK_TOKEN = "sk-abcdefghijklmnopqrstuvwxyz"


# ── A. redact_secrets() 的 URL 内嵌凭据脱敏 ──────────────────────────────────

def test_redacts_password_in_postgres_url_while_keeping_scheme_user_and_host():
    """postgresql://user:pass@host 的口令被打掉，scheme/user/host 仍可见（审批可知情的前提）。"""
    # Arrange
    from app.services.security_authorization import redact_secrets
    text = f"postgresql://dbadmin:{FAKE_DB_PASSWORD}@db.internal:5432/appdb"

    # Act
    out = redact_secrets(text)

    # Assert — 口令消失
    assert FAKE_DB_PASSWORD not in out
    assert "[REDACTED]" in out
    # Assert — 设计意图：审批者仍须看出「连哪个库、用哪个账号」
    assert "postgresql://" in out
    assert "dbadmin" in out
    assert "db.internal" in out
    assert out == "postgresql://dbadmin:[REDACTED]@db.internal:5432/appdb"


def test_redacts_credentials_in_scheme_containing_plus_sign():
    """scheme 含 `+`（mysql+pymysql://）也被覆盖 —— 模式允许 + . - 三种字符。"""
    # Arrange
    from app.services.security_authorization import redact_secrets
    text = f"mysql+pymysql://appuser:{FAKE_DB_PASSWORD}@mysql-01.internal:3306/orders"

    # Act
    out = redact_secrets(text)

    # Assert
    assert FAKE_DB_PASSWORD not in out
    assert out == "mysql+pymysql://appuser:[REDACTED]@mysql-01.internal:3306/orders"


def test_redacts_credentials_in_https_url():
    """https:// 里的 basic-auth 凭据同样被脱敏（不限于数据库连接串）。"""
    # Arrange
    from app.services.security_authorization import redact_secrets
    text = f"curl https://svcaccount:{FAKE_API_KEY_VALUE}@api.example.com/v1/deploy"

    # Act
    out = redact_secrets(text)

    # Assert
    assert FAKE_API_KEY_VALUE not in out
    assert "[REDACTED]" in out
    assert "https://svcaccount:" in out
    assert "api.example.com" in out


@pytest.mark.parametrize("benign", [
    "https://example.com/path?q=1",
    "postgresql://db.internal/appdb",
    "postgresql://db.internal:5432/appdb",     # 端口号不是口令
    "git+https://github.com/acme/repo.git",
    "https://example.com:8080/path@anchor",    # 见 docstring：这条守着匹配边界
    "see docs at https://host:8080/a/b",
    "mailto:user@host.com",                    # 无 `://`，不应匹配
])
def test_leaves_url_without_credentials_untouched(benign):
    """无凭据的 URL 一字不改 —— 防误伤（脱敏过度会重新让审批者看不见内容）。

    这组用例锁死的是「**不得为覆盖 `redis://:pass@host` 而扩大匹配**」这条边界。
    曾评估过把 userinfo 放开 `/` 并贪到最后一个 `@`（`([a-z][a-z0-9+.\\-]*://[^\\s@]*?):([^\\s@]*)@`），
    实测它把 `https://example.com:8080/path@anchor` 改成
    `https://example.com:[REDACTED]@anchor` —— 端口 + 路径被判成口令，合法 URL 被摧毁。
    脱敏的误伤比漏检更难发现（内容被无声抹掉且无人核对），故口令段固定排除 `/`。
    **重构 `_URL_CRED_PATTERN` 前先看这组用例**：任何让下面某条产出 `[REDACTED]` 的
    改动都是退步，即便它顺带覆盖了更多凭据形态。
    """
    # Arrange
    from app.services.security_authorization import redact_secrets

    # Act
    out = redact_secrets(benign)

    # Assert
    assert out == benign, f"无凭据 URL 被误改: {benign!r} -> {out!r}"
    assert "[REDACTED]" not in out


def test_redacts_url_credentials_alongside_bare_token_and_api_key_assignment():
    """三条旧模式与新 URL 模式共存：同一段文本里的 sk- token、api_key=、URL 口令全部脱敏。"""
    # Arrange
    from app.services.security_authorization import redact_secrets
    text = (
        f"deploy --token {FAKE_SK_TOKEN} "
        f"--api_key={FAKE_API_KEY_VALUE} "
        f"--dsn postgresql://dbadmin:{FAKE_DB_PASSWORD}@db.internal:5432/appdb"
    )

    # Act
    out = redact_secrets(text)

    # Assert — 三种形态一个都不剩
    assert FAKE_SK_TOKEN not in out
    assert FAKE_API_KEY_VALUE not in out
    assert FAKE_DB_PASSWORD not in out
    assert out.count("[REDACTED]") >= 3
    # Assert — 非敏感上下文仍保留
    assert "deploy" in out
    assert "db.internal" in out


@pytest.mark.parametrize("scheme,host", [
    ("redis", "cache.internal:6379/0"),
    ("mongodb", "mongo.internal:27017/appdb"),
    ("amqp", "rabbit.internal:5672//"),
])
def test_redacts_password_only_userinfo_url(scheme, host):
    """password-only userinfo（scheme://:pass@host，无用户名）里的口令被脱敏。

    B-R20-URL-CRED-EMPTY-USER：`_URL_CRED_PATTERN` 初版用户名段是 `[^\\s:/@]+`
    （一个或多个），空用户名不匹配，`redis://:pass@host` 完全漏过（旧三条模式也不覆盖）。
    这是 Redis / Celery broker / Django CACHES 连接串的**标准写法**（Redis 6 ACL 之前
    只有 AUTH 口令、无用户名），`mongodb://` / `amqp://` 同形，故该口令会经
    `_redacted_action_payload` 原样写进 Gate summary 与审计。量词改 `*` 后覆盖。
    """
    # Arrange
    from app.services.security_authorization import redact_secrets
    text = f"connect {scheme}://:{FAKE_DB_PASSWORD}@{host}"

    # Act
    out = redact_secrets(text)

    # Assert — 口令消失
    assert FAKE_DB_PASSWORD not in out, f"password-only userinfo 未脱敏: {out!r}"
    # Assert — 空用户名下替换式仍产出 `scheme://:[REDACTED]@`，host 仍可见
    assert out == f"connect {scheme}://:[REDACTED]@{host}"


def test_redact_secrets_returns_empty_input_unchanged():
    """空字符串/None 走短路分支，不抛异常。"""
    # Arrange
    from app.services.security_authorization import redact_secrets

    # Act / Assert
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None


# ── B. _redacted_action_payload() 的行为 ─────────────────────────────────────

NO_ARGS_TEXT = "（无入参）"
FAIL_CLOSED_TEXT = "（入参未展示：脱敏组件不可用，为避免凭据落盘已省略）"


def test_payload_reports_no_args_for_none_and_empty_dict():
    """args=None 与 args={} 都渲染为「（无入参）」，而不是 'None' / '{}'。"""
    # Arrange
    from app.services.tool_registry import _redacted_action_payload

    # Act
    from_none = _redacted_action_payload("execute_command", None)
    from_empty = _redacted_action_payload("execute_command", {})

    # Assert
    assert from_none == NO_ARGS_TEXT
    assert from_empty == NO_ARGS_TEXT


def test_payload_includes_argument_names_and_non_sensitive_values():
    """正常入参：参数名与非敏感值都出现在摘要里（审批者据此判断 Agent 要做什么）。"""
    # Arrange
    from app.services.tool_registry import _redacted_action_payload
    args = {"path": "workspace/source/app.py", "mode": "overwrite", "lines": 42}

    # Act
    out = _redacted_action_payload("write_file", args)

    # Assert
    assert "path" in out
    assert "workspace/source/app.py" in out
    assert "mode" in out
    assert "overwrite" in out
    assert "42" in out


def test_payload_redacts_url_credentials_inside_command_argument():
    """含连接串凭据的入参：口令不出现，但库/账号仍可见（脱敏但仍可审批）。"""
    # Arrange
    from app.services.tool_registry import _redacted_action_payload
    args = {"command": f"psql postgresql://dbadmin:{FAKE_DB_PASSWORD}@db.internal:5432/appdb -c 'select 1'"}

    # Act
    out = _redacted_action_payload("execute_command", args)

    # Assert — 敏感段不落盘
    assert FAKE_DB_PASSWORD not in out
    assert "[REDACTED]" in out
    # Assert — 可审批性保留
    assert "psql" in out
    assert "dbadmin" in out
    assert "db.internal" in out


def test_payload_redacts_api_key_style_argument_value():
    """`api_key=` 形态的入参值同样被脱敏（复用既有三条模式，不另造实现）。"""
    # Arrange
    from app.services.tool_registry import _redacted_action_payload
    args = {"command": f"deploy --api_key={FAKE_API_KEY_VALUE} --target prod"}

    # Act
    out = _redacted_action_payload("execute_command", args)

    # Assert
    assert FAKE_API_KEY_VALUE not in out
    assert "[REDACTED]" in out
    assert "deploy" in out


def test_payload_truncates_oversized_args_and_reports_original_length():
    """超过 max_chars 时截断、带截断标记、并报出原长（避免 Gate summary 无界膨胀）。"""
    # Arrange
    from app.services.tool_registry import _redacted_action_payload
    max_chars = 120
    args = {"content": "A" * 500}

    # Act
    out = _redacted_action_payload("write_file", args, max_chars=max_chars)

    # Assert — 有截断标记
    assert "已截断" in out
    assert "原长" in out
    # Assert — 正文被切到 max_chars
    head, _, tail = out.partition("…［已截断")
    assert len(head) == max_chars
    # Assert — 报出的原长确实大于 max_chars
    reported = re.search(r"原长 (\d+) 字符", out)
    assert reported is not None, f"未报出原长: {out!r}"
    assert int(reported.group(1)) > max_chars


def test_payload_is_not_truncated_when_within_limit():
    """未超限时不加截断标记（防止截断逻辑误伤短入参）。"""
    # Arrange
    from app.services.tool_registry import _redacted_action_payload

    # Act
    out = _redacted_action_payload("read_file", {"path": "a.py"}, max_chars=600)

    # Assert
    assert "已截断" not in out
    assert "a.py" in out


def test_payload_fails_closed_when_redactor_raises(monkeypatch):
    """安全红线：脱敏组件抛异常时不得原样返回入参，而须返回 fail-closed 文案。"""
    # Arrange — 让 redact_secrets 抛异常（_redacted_action_payload 在调用时才 import 它，
    # 故打模块属性即可命中）
    from app.services.tool_registry import _redacted_action_payload

    def _boom(_text):
        raise RuntimeError("redactor unavailable (injected)")

    monkeypatch.setattr("app.services.security_authorization.redact_secrets", _boom)
    args = {"command": f"psql postgresql://dbadmin:{FAKE_DB_PASSWORD}@db.internal/appdb"}

    # Act
    out = _redacted_action_payload("execute_command", args)

    # Assert — 原样返回入参是不可接受的
    assert out == FAIL_CLOSED_TEXT
    assert FAKE_DB_PASSWORD not in out
    assert "dbadmin" not in out
    assert "postgresql" not in out


def test_payload_fails_closed_when_redactor_module_missing(monkeypatch):
    """脱敏模块整体不可导入时同样 fail-closed（不是只有「函数抛异常」一条路径）。"""
    # Arrange — 让 `from app.services.security_authorization import redact_secrets` 失败
    import builtins
    from app.services.tool_registry import _redacted_action_payload
    real_import = builtins.__import__

    def _blocking_import(name, *a, **kw):
        if name == "app.services.security_authorization":
            raise ImportError("module unavailable (injected)")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    args = {"command": f"deploy --api_key={FAKE_API_KEY_VALUE}"}

    # Act
    out = _redacted_action_payload("execute_command", args)

    # Assert
    assert out == FAIL_CLOSED_TEXT
    assert FAKE_API_KEY_VALUE not in out


# ── C. Gate 层端到端：入参真的进了 Gate，且是脱敏后的形态 ─────────────────────

SENSITIVE_COMMAND = (
    f"psql postgresql://dbadmin:{FAKE_DB_PASSWORD}@db.internal:5432/appdb "
    f"--api_key={FAKE_API_KEY_VALUE}"
)


def test_risk_gate_summary_carries_redacted_payload(isolated_data):
    """_create_risk_gate 创建的 L4 Gate，其 summary 含脱敏入参、不含原始凭据。"""
    # Arrange
    from app.dependencies import get_services
    from app.services.tool_registry import _create_risk_gate
    project_id = "p-r20-gate-payload"
    args = {"command": SENSITIVE_COMMAND, "cwd": "workspace/source"}

    # Act
    out = _create_risk_gate(project_id, "run-r20", "P4", "execute_command", "L4", args=args)

    # Assert — 走的是真实 Gate 路径（不是降级）
    assert out["status"] == "awaiting_approval", out
    assert out["gate_type"] == "action_approval"
    gate = get_services().gate_service.get(out["gate_id"])
    assert gate is not None
    # Assert — 入参确实进了 summary（修法的目的：审批可知情）
    assert "待执行入参" in gate.summary
    assert "psql" in gate.summary
    assert "dbadmin" in gate.summary
    assert "db.internal" in gate.summary
    assert "workspace/source" in gate.summary
    # Assert — 但原始凭据不落盘
    assert FAKE_DB_PASSWORD not in gate.summary
    assert FAKE_API_KEY_VALUE not in gate.summary
    assert "[REDACTED]" in gate.summary


def test_risk_gate_without_args_still_creates_gate_with_no_args_marker(isolated_data):
    """无入参时 Gate 仍照常创建，summary 标注「（无入参）」而不是留空。"""
    # Arrange
    from app.dependencies import get_services
    from app.services.tool_registry import _create_risk_gate

    # Act
    out = _create_risk_gate("p-r20-noargs", "run-r20", "P4", "delete_artifact", "L4")

    # Assert
    assert out["status"] == "awaiting_approval", out
    gate = get_services().gate_service.get(out["gate_id"])
    assert NO_ARGS_TEXT in gate.summary


def test_risk_gate_degraded_branch_returns_redacted_action_payload(isolated_data, monkeypatch):
    """诚实降级分支（拿不到 GateService）的 action_payload 字段同样脱敏，且不伪造 gate_id。"""
    # Arrange — 让 get_services() 抛异常，逼 _create_risk_gate 走 risk_flagged 分支
    import app.dependencies as deps

    def _no_services(*a, **kw):
        raise RuntimeError("no service container in this runtime context (injected)")

    monkeypatch.setattr(deps, "get_services", _no_services)
    args = {"command": SENSITIVE_COMMAND}

    # Act
    from app.services.tool_registry import _create_risk_gate
    out = _create_risk_gate("p-r20-degraded", "run-r20", "P4", "execute_command", "L4", args=args)

    # Assert — 诚实降级，不伪造 gate
    assert out["status"] == "risk_flagged"
    assert "gate_id" not in out
    # Assert — 降级返回里也带入参，且已脱敏
    payload = out["action_payload"]
    assert FAKE_DB_PASSWORD not in payload
    assert FAKE_API_KEY_VALUE not in payload
    assert "[REDACTED]" in payload
    assert "dbadmin" in payload
    # Assert — 整个返回结构里没有任何原始凭据残留
    blob = repr(out)
    assert FAKE_DB_PASSWORD not in blob
    assert FAKE_API_KEY_VALUE not in blob


def test_agent_loop_action_gate_summary_carries_redacted_payload(isolated_data):
    """agent_loop._create_action_gate 复用同一渲染器：summary 含脱敏入参、不含原始凭据。"""
    # Arrange
    from app.dependencies import get_services
    from app.services.agent_loop import AgentLoop
    loop = AgentLoop.__new__(AgentLoop)   # 只测 Gate 渲染，不需要完整 LLM 依赖
    args = {"command": SENSITIVE_COMMAND}

    # Act
    gate_id = loop._create_action_gate(
        "p-r20-agentloop", "run-r20", "P4", "execute_command", "L4",
        "Manual 模式受控动作", args=args)

    # Assert
    assert gate_id, "action_approval Gate 未创建"
    gate = get_services().gate_service.get(gate_id)
    assert "待执行入参" in gate.summary
    assert FAKE_DB_PASSWORD not in gate.summary
    assert FAKE_API_KEY_VALUE not in gate.summary
    assert "[REDACTED]" in gate.summary
    assert "dbadmin" in gate.summary
