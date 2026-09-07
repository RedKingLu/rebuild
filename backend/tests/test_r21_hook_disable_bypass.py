"""B-R21-HOOK-DISABLE-BYPASS / B-R20-REDACT-THREE-IMPLS 回归测试。

背景（事实，见任务交底）：

1) `hook_engine._load_hooks()` 只加载 `ResourceEntry.enabled == True` 且
   `status == active` 的 hook 行；`PATCH /resources/{id}/disable` 对**任意**资源零风险
   分级、零 Gate 审批、零审计记录即可把 `"pre-write Policy check"` 那一行的 `enabled`
   翻成 `False`——从而让 D-032（禁止把明文密钥/凭据写入工作区产物）唯一的内容级检测
   （`run_hooks` 驱动的 `_impl_pre_write_policy`）在 Registry 层整体失效。
   `workspace_mediator` 对 D-099①（source/ 只读）有独立、无条件的第二层防护
   （`_READONLY_DIRS` 硬编码检查），但 D-032 此前没有——是单点失效。
   本文件第 1 组验证：`tool_registry.execute_tool` 新增的、**不查询 DB enabled 状态**的
   代码层强制调用（`hook_engine.enforce_pre_write_policy`）即便在该 `ResourceEntry.enabled
   =False` 时仍生效（fail-closed，不依赖 DB 开关）。

2) `hook_engine` 曾维护一份自己的 `_SECRET_PATTERNS`（3 条，旧副本），比
   `security_authorization._SECRET_PATTERNS`（4 条，多一条 URL 内嵌凭据模式）少一条，
   导致 D-032 的 `block` 模式硬线对 `postgresql://user:pass@host` /
   `redis://:pass@host` 这类连接串口令**整体不设防**（B-R20-REDACT-THREE-IMPLS）。
   本文件第 2 组验证 `hook_engine` 现在复用 `security_authorization.contains_secret()`
   单一实现，URL 凭据形态也被拦截；第 3 组是防误伤用例——对齐
   `test_r20_gate_payload_redaction.py` 已有的 7 条防误伤基准，验证合法内容（不含凭据的
   URL/连接串示例，如产出文档里的示例连接串）不会被新模式误拦（摸爆炸半径结论：全仓
   grep 未发现会经写入路径落盘的真实凭据示例，详见任务报告）。

注：本文件所有"看起来像凭据"的字符串均为合成假值，非真实凭据（AGENTS.md §8）。
"""

import asyncio
import uuid

import pytest  # noqa: F401  (pytest-asyncio auto mode collects async tests)

from app.core.database import get_session
from app.models.resource_entry import (
    ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus,
)
from app.services import tool_registry
from app.services.workspace_service import workspace_path

_SECURITY_HOOK_NAME = "pre-write Policy check"

# 合成假凭据（非真实凭据），仅用于触发/验证 D-032 写入期拦截
_FAKE_DB_PASSWORD = "Pa55w0rdFAKE"
_FAKE_SECRET_CONTENT = "API_KEY=sk-FAKEfakefakefakefake1234567890"


def _add_disabled_security_hook(db):
    """插入一条【已禁用】的 pre-write Policy check hook 行——模拟
    `PATCH /resources/{id}/disable` 已把它关掉之后的数据库状态（对照真实 seed.py:213
    形态：`type_metadata` 含 `hook_impl`，只是 `enabled=False`）。"""
    db.add(ResourceEntry(
        resource_id=str(uuid.uuid4()), name=_SECURITY_HOOK_NAME,
        resource_type=ResourceType.hook, source_type=SourceType.internal_current,
        source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
        status=ResourceStatus.active, enabled=False,
        description="B-R21 回归夹具：模拟已被 PATCH /disable 关闭的安全 hook",
        type_metadata={"hook_point": "PreToolUse", "hook_mode": "block",
                       "hook_impl": "pre_write_policy"}))
    db.commit()


def _add_write_tool(db, tool_name: str = "fs_write_artifact"):
    db.add(ResourceEntry(
        resource_id=str(uuid.uuid4()), name=tool_name,
        resource_type=ResourceType.tool, source_type=SourceType.user_provided,
        source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
        status=ResourceStatus.active, enabled=True, description="write",
        type_metadata={"tool_name": tool_name, "write_scope": "workspace"}))
    db.commit()


# ── 1. 代码层强制检查不受 ResourceEntry.enabled 影响（核心不变量） ──────────────

def test_secret_write_still_blocked_when_hook_resource_disabled(isolated_data):
    """核心不变量：即便 "pre-write Policy check" 这一行 enabled=False，execute_tool
    仍必须拦截明文密钥写入。先用一个对照组证明 Registry 驱动层确实已经失效
    （即测试真的复现了原漏洞场景，而不是巧合通过）。"""
    db = get_session()
    try:
        _add_disabled_security_hook(db)
        _add_write_tool(db)

        from app.services.hook_engine import run_hooks
        registry_outcome = run_hooks("PreToolUse", {
            "project_id": "p-r21", "tool_name": "fs_write_artifact",
            "write_scope": "workspace",
            "args": {"path": "output_code/leak.py", "content": _FAKE_SECRET_CONTENT},
        }, db)
        assert registry_outcome.blocked is False, (
            "对照组失败：禁用行竟仍被 Registry 驱动层加载，未复现漏洞前提")

        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact",
            {"path": "output_code/leak.py", "content": _FAKE_SECRET_CONTENT},
            project_id="p-r21-e2e", stage="p4", db=db))
        assert result.get("status") == "blocked_by_hook", result
        assert not (workspace_path("p-r21-e2e") / "output_code" / "leak.py").exists()
    finally:
        db.close()


def test_url_credential_write_still_blocked_when_hook_resource_disabled(isolated_data):
    """同上，触发内容是 URL 内嵌凭据形态（旧 hook_engine 私有模式表原本就漏检这条，
    B-R20-REDACT-THREE-IMPLS）。"""
    db = get_session()
    try:
        _add_disabled_security_hook(db)
        _add_write_tool(db)
        content = f"DATABASE_URL=postgresql://dbadmin:{_FAKE_DB_PASSWORD}@db.internal:5432/appdb"
        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact", {"path": "output_code/cfg.py", "content": content},
            project_id="p-r21-url", stage="p4", db=db))
        assert result.get("status") == "blocked_by_hook", result
        assert not (workspace_path("p-r21-url") / "output_code" / "cfg.py").exists()
    finally:
        db.close()


def test_source_write_still_blocked_when_hook_resource_disabled(isolated_data):
    """D-099① source/ 只读拦截同样走这条不可关闭调用，禁用行不影响其生效
    （workspace_mediator 已有独立第二层防护；这里验证 hook 层不因禁用而失守）。"""
    db = get_session()
    try:
        _add_disabled_security_hook(db)
        _add_write_tool(db)
        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact", {"path": "source/x.py", "content": "print(1)"},
            project_id="p-r21-src", stage="p4", db=db))
        assert result.get("status") == "blocked_by_hook", result
    finally:
        db.close()


def test_clean_write_allowed_when_hook_resource_disabled(isolated_data):
    """无过度 fail-closed：干净内容即便在"该 hook 行已禁用"场景下也应正常写入成功。"""
    db = get_session()
    try:
        _add_disabled_security_hook(db)
        _add_write_tool(db)
        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact", {"path": "output_code/clean.py", "content": "print('hi')"},
            project_id="p-r21-clean", stage="p4", db=db))
        assert result.get("status") != "blocked_by_hook", result
        assert (workspace_path("p-r21-clean") / "output_code" / "clean.py").exists()
    finally:
        db.close()


def test_hard_check_engine_failure_fails_closed(isolated_data, monkeypatch):
    """代码层强制检查本体若自身异常，不得 fail-open 放行——这是不可关闭的最后一道
    防线，与"Registry 驱动 hook 引擎故障 fail-open"的既有策略刻意不同。"""
    db = get_session()
    try:
        _add_write_tool(db)

        def _boom(_ctx):
            raise RuntimeError("engine failure (injected)")

        monkeypatch.setattr("app.services.hook_engine.enforce_pre_write_policy", _boom)
        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact", {"path": "output_code/x.py", "content": "print(1)"},
            project_id="p-r21-engine-fail", stage="p4", db=db))
        assert result.get("status") == "blocked_by_hook", result
    finally:
        db.close()


def test_enforce_pre_write_policy_delegates_to_same_check_body():
    """`enforce_pre_write_policy` 与 `_impl_pre_write_policy` 对同一输入必须给出同一结论
    ——防止未来有人给"不可关闭版本"另写一套逻辑，导致两条路径行为漂移。"""
    from app.services.hook_engine import enforce_pre_write_policy, _impl_pre_write_policy
    ctx = {"write_scope": "workspace",
           "args": {"path": "output_code/x.py", "content": _FAKE_SECRET_CONTENT}}
    assert enforce_pre_write_policy(ctx) == _impl_pre_write_policy(ctx)


# ── 2. hook_engine 复用 security_authorization 单一实现（URL 凭据形态可拦） ──────

@pytest.mark.parametrize("content", [
    f"postgresql://dbadmin:{_FAKE_DB_PASSWORD}@db.internal:5432/appdb",
    f"redis://:{_FAKE_DB_PASSWORD}@cache.internal:6379/0",
    f"mongodb://:{_FAKE_DB_PASSWORD}@mongo.internal:27017/appdb",
    f"mysql+pymysql://appuser:{_FAKE_DB_PASSWORD}@mysql-01.internal:3306/orders",
])
def test_pre_write_policy_blocks_url_embedded_credentials(content):
    """B-R20-REDACT-THREE-IMPLS 直接回归：这四类 URL 内嵌凭据形态曾被 hook_engine
    自己的旧 3-模式表放行，现在必须被拦。"""
    from app.services.hook_engine import _impl_pre_write_policy
    action, reason = _impl_pre_write_policy({
        "write_scope": "workspace",
        "args": {"path": "output_code/cfg.py", "content": content},
    })
    assert action == "block", f"URL 内嵌凭据未被拦截: {content!r}"
    assert "密钥" in reason or "凭据" in reason


def test_hook_engine_no_longer_keeps_a_private_secret_pattern_list():
    """结构性回归锁：hook_engine 不得再维护自己的 `_SECRET_PATTERNS` 副本
    （防止未来又漂移出第三份独立维护的模式表）。"""
    import app.services.hook_engine as hook_engine
    assert not hasattr(hook_engine, "_SECRET_PATTERNS"), (
        "hook_engine 又出现了私有 _SECRET_PATTERNS 副本，应复用 "
        "security_authorization.contains_secret()（单一事实源）")


def test_hook_engine_imports_contains_secret_from_security_authorization():
    """hook_engine 的密钥检测应直接复用 security_authorization.contains_secret，
    而不是另造一个同名但独立维护的判定函数。"""
    import app.services.hook_engine as hook_engine
    import app.services.security_authorization as sec_auth
    assert hook_engine.contains_secret is sec_auth.contains_secret


# ── 3. 防误伤：合法内容不得被新模式误拦 ──────────────────────────────────────
# 对齐 test_r20_gate_payload_redaction.test_leaves_url_without_credentials_untouched
# 的 7 条防误伤基准——同一组用例，换一个调用面（pre-write 写入检查而非 Gate 脱敏）。

@pytest.mark.parametrize("benign_content", [
    "https://example.com/path?q=1",
    "postgresql://db.internal/appdb",
    "postgresql://db.internal:5432/appdb",     # 端口号不是口令
    "git+https://github.com/acme/repo.git",
    "https://example.com:8080/path@anchor",    # 端口+路径不得被误判为口令
    "see docs at https://host:8080/a/b",
    "mailto:user@host.com",                    # 无 `://`，不应匹配
])
def test_pre_write_policy_allows_benign_url_content(benign_content):
    """合法示例连接串/URL（不含凭据）写入不应被拦——防止收紧后误伤产出文档里的
    示例连接串、迁移方案里的目标库 DSN 样例。"""
    from app.services.hook_engine import _impl_pre_write_policy
    action, _ = _impl_pre_write_policy({
        "write_scope": "workspace",
        "args": {"path": "artifacts/p3/design_note.md", "content": benign_content},
    })
    assert action == "allow", f"合法内容被误拦: {benign_content!r}"


def test_execute_tool_allows_benign_dsn_example_in_docs_write(isolated_data):
    """端到端：产出文档里常见的"目标库 DSN 示例"（无凭据）经 execute_tool 写入不受影响
    （摸爆炸半径结论的端到端锚点，对应任务报告中的爆炸半径排查）。"""
    db = get_session()
    try:
        _add_write_tool(db)
        content = "目标库连接串示例：postgresql://db.internal:5432/appdb（无口令，仅展示格式）"
        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact", {"path": "output_code/note.md", "content": content},
            project_id="p-r21-benign", stage="p4", db=db))
        assert result.get("status") != "blocked_by_hook", result
    finally:
        db.close()
