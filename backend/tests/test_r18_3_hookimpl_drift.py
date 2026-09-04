"""B-R18-3-HOOKIMPL-DRIFT 回归测试（P0 安全缺陷）。

背景（事实，见 证据/进度追踪/02-阻塞项.md B-R18-3-HOOKIMPL-DRIFT）：
真实库 `backend/.data/rebuild.db` 中 `pre-write Policy check` 行的 type_metadata 原文为
``{"hook_point": "PreToolUse", "hook_mode": "block"}`` —— **没有 `hook_impl` 键**（早期 seed
版本留下、seed 不回填已存在行 ⇒ seed 漂移）。旧 `run_hooks` 以 `meta["hook_impl"]` 为唯一
绑定依据，取不到即 `logger.debug` 静默 `continue`，导致 `_impl_pre_write_policy` 承载的
**D-032 明文密钥写入拦截在真实库上根本不执行**；而 conftest 每次用 tmp 库跑 seed（带
`hook_impl`）⇒ 测试全绿。同型教训：R11-3 / R14-5「tests 绿 ≠ 真实库正常」、OBS-REALDB-DRIFT-R15。

本文件的所有 hook 行都**按真实库的元数据原文构造**（即缺 `hook_impl`），因此它测的是
真实库形态而非 seed 形态。第 5 组另有一条直接读真实库文件（只读）的断言。

注：测试中的 `sk-FAKE...` / `AKIA...` 等均为**合成假值**，非真实凭据（AGENTS.md §8）。
"""

import asyncio
import logging
import uuid

import pytest

# 真实库该行 type_metadata 的原文（逐字符对齐实测结果，缺 hook_impl 键）
_REAL_DB_META = {"hook_point": "PreToolUse", "hook_mode": "block"}
_SECURITY_HOOK_NAME = "pre-write Policy check"

# 合成假密钥值（非真实凭据），仅用于触发 D-032 写入期拦截
_FAKE_SECRET_CONTENT = "API_KEY=sk-FAKEfakefakefakefake1234567890"


@pytest.fixture(autouse=True)
def _revive_hook_engine_logger():
    """复活 `rebuild.hook_engine` logger —— **test-only，不动生产代码**。

    本文件的四条日志断言用例在**单跑时通过、全量套件中失败**。根因不在被测代码：
    `backend/alembic/env.py:14` 的 `fileConfig(config.config_file_name)` 默认
    `disable_existing_loggers=True`，会把**此前已导入**的 logger 置 `disabled=True`；
    全量套件里迁移相关用例一跑过，`rebuild.hook_engine` 就被禁言，`caplog` 遂抓不到
    任何 record。而 `caplog.at_level()` 只调整级别，**不会**把已被禁用的 logger 重新启用、
    也不修 `propagate`。

    本项目已踩过同一个坑并有成熟解法：`B-R17.2-SSE-TEST-LEAK`（见
    `证据/进度追踪/02-阻塞项.md`），修复范式见 `tests/test_r17_2_sse_events.py:95-100`。
    此处沿用该范式，**仅复位 logger 的可发声状态，不放宽任何断言**。
    """
    lg = logging.getLogger("rebuild.hook_engine")
    lg.disabled = False
    lg.propagate = True
    yield


def _add_hook_row(db, name: str, meta: dict, *, status=None, enabled: bool = True):
    """按给定元数据插入一条 hook 资源行并提交。"""
    from app.models.resource_entry import (
        ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus)
    db.add(ResourceEntry(
        resource_id=str(uuid.uuid4()), name=name,
        resource_type=ResourceType.hook, source_type=SourceType.user_provided,
        source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
        status=status or ResourceStatus.active, enabled=enabled,
        description="B-R18-3 回归夹具", type_metadata=dict(meta)))
    db.commit()


def _write_ctx(path: str, content: str) -> dict:
    return {"project_id": "p-r18-3", "tool_name": "fs_write_artifact",
            "write_scope": "workspace", "args": {"path": path, "content": content}}


# ── 1. 缺 hook_impl 时安全 hook 仍必须生效（根因修复） ──────────────────────

def test_secret_write_blocked_when_hook_impl_metadata_missing(isolated_data):
    """真实库形态（元数据缺 hook_impl）下，D-032 明文密钥写入必须被拦。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME, _REAL_DB_META)
        outcome = run_hooks("PreToolUse", _write_ctx("output_code/leak.py",
                                                     _FAKE_SECRET_CONTENT), db)
        assert outcome.blocked is True, "元数据缺 hook_impl 时 D-032 写入期拦截未执行"
        assert "密钥" in outcome.block_reason or "凭据" in outcome.block_reason
        assert _SECURITY_HOOK_NAME in [r.hook_name for r in outcome.results]
    finally:
        db.close()


def test_aws_style_secret_write_blocked_when_hook_impl_metadata_missing(isolated_data):
    """同上，另一类密钥形态（合成假 AKIA 串）。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME, _REAL_DB_META)
        outcome = run_hooks("PreToolUse",
                            _write_ctx("output_code/cfg.py", "k = 'AKIAFAKEFAKEFAKEFAKE'"), db)
        assert outcome.blocked is True
    finally:
        db.close()


def test_source_write_blocked_when_hook_impl_metadata_missing(isolated_data):
    """source/ 只读拦截（D-099①）在真实库形态下同样生效。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME, _REAL_DB_META)
        outcome = run_hooks("PreToolUse", _write_ctx("source/x.py", "print(1)"), db)
        assert outcome.blocked is True
    finally:
        db.close()


def test_clean_write_and_read_tool_still_pass_when_hook_impl_missing(isolated_data):
    """确定性绑定不得把干净写入/只读工具误拦（无过度 fail-closed）。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME, _REAL_DB_META)
        ok = run_hooks("PreToolUse", _write_ctx("output_code/clean.py", "print('hi')"), db)
        assert ok.blocked is False
        rd = run_hooks("PreToolUse", {"project_id": "p-r18-3", "tool_name": "fs_read",
                                      "write_scope": "none",
                                      "args": {"path": "source/x.py"}}, db)
        assert rd.blocked is False
    finally:
        db.close()


def test_explicit_hook_impl_metadata_still_honored(isolated_data):
    """向后兼容：元数据显式给了 hook_impl（tmp 库/seed 形态）时行为不变。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME,
                      {"hook_point": "PreToolUse", "hook_mode": "block",
                       "hook_impl": "pre_write_policy"})
        outcome = run_hooks("PreToolUse", _write_ctx("output_code/leak.py",
                                                     _FAKE_SECRET_CONTENT), db)
        assert outcome.blocked is True
    finally:
        db.close()


def test_tool_registry_blocks_secret_write_when_hook_impl_missing(isolated_data):
    """端到端：execute_tool 在真实库形态下也必须 blocked_by_hook 且文件未落盘。"""
    from app.core.database import get_session
    from app.models.resource_entry import (
        ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus)
    from app.services import tool_registry
    from app.services.workspace_service import workspace_path
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME, _REAL_DB_META)
        db.add(ResourceEntry(
            resource_id=str(uuid.uuid4()), name="fs_write_artifact",
            resource_type=ResourceType.tool, source_type=SourceType.user_provided,
            source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L2,
            status=ResourceStatus.active, enabled=True, description="write",
            type_metadata={"tool_name": "fs_write_artifact", "write_scope": "workspace"}))
        db.commit()
        result = asyncio.run(tool_registry.execute_tool(
            "fs_write_artifact",
            {"path": "output_code/leak.py", "content": _FAKE_SECRET_CONTENT},
            project_id="p-r18-3-e2e", stage="p4", db=db))
        assert result.get("status") == "blocked_by_hook"
        assert not (workspace_path("p-r18-3-e2e") / "output_code" / "leak.py").exists()
    finally:
        db.close()


# ── 2. 解析失败必须发声（公理 3）且 block 模式 fail-closed ──────────────────

def test_unresolvable_block_mode_hook_fails_closed_and_speaks(isolated_data, caplog):
    """block 模式 hook 取不到实现体 ⇒ fail-closed（视为 block）且发声 ≥ WARNING。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, "some unknown block hook",
                      {"hook_point": "PreToolUse", "hook_mode": "block",
                       "hook_impl": "no_such_impl"})
        with caplog.at_level(logging.DEBUG, logger="rebuild.hook_engine"):
            outcome = run_hooks("PreToolUse",
                                _write_ctx("output_code/a.py", "print(1)"), db)
        assert outcome.blocked is True, "block 模式 hook 解析失败仍放行（fail-open）"
        assert outcome.block_reason, "fail-closed 必须给出明确 reason"
        spoken = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert spoken, "解析失败被静默（应 ≥ WARNING 发声）"
    finally:
        db.close()


def test_unresolvable_warn_mode_hook_skips_but_speaks(isolated_data, caplog):
    """warn 模式 hook 取不到实现体 ⇒ 可继续跳过，但必须发声 ≥ WARNING。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, "some unknown warn hook",
                      {"hook_point": "PreToolUse", "hook_mode": "warn",
                       "hook_impl": "no_such_impl"})
        with caplog.at_level(logging.DEBUG, logger="rebuild.hook_engine"):
            outcome = run_hooks("PreToolUse",
                                _write_ctx("output_code/a.py", "print(1)"), db)
        assert outcome.blocked is False, "warn 模式不应阻断"
        spoken = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert spoken, "warn 模式解析失败被静默（应 ≥ WARNING 发声）"
        assert outcome.warnings, "warn 模式解析失败应记入 outcome.warnings"
    finally:
        db.close()


def test_typo_hook_impl_on_known_security_hook_still_binds(isolated_data):
    """已知安全 hook 的 hook_impl 写错时，按名称兜底绑定（不得因元数据笔误失守）。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME,
                      {"hook_point": "PreToolUse", "hook_mode": "block",
                       "hook_impl": "pre_write_policyy"})  # 笔误
        outcome = run_hooks("PreToolUse", _write_ctx("output_code/leak.py",
                                                     _FAKE_SECRET_CONTENT), db)
        assert outcome.blocked is True
    finally:
        db.close()


# ── 3. hook_mode 缺省值收紧（仅对安全类实现体） ────────────────────────────

def test_security_hook_without_hook_mode_defaults_to_block(isolated_data):
    """安全类 hook 元数据缺 hook_mode 时缺省为 block（不再 fail-open 到 warn）。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME, {"hook_point": "PreToolUse"})
        outcome = run_hooks("PreToolUse", _write_ctx("output_code/leak.py",
                                                     _FAKE_SECRET_CONTENT), db)
        assert outcome.blocked is True
    finally:
        db.close()


def test_non_security_hook_without_hook_mode_stays_warn(isolated_data, caplog):
    """非安全类 hook 缺 hook_mode 时仍缺省 warn（保持既有设计语义，不牵连放大 fail-closed）。"""
    from app.core.database import get_session
    from app.services.hook_engine import run_hooks
    db = get_session()
    try:
        _add_hook_row(db, "pre-commit quality check", {"hook_point": "PreToolUse"})
        with caplog.at_level(logging.DEBUG, logger="rebuild.hook_engine"):
            outcome = run_hooks("PreToolUse", _write_ctx("output_code/a.py", "print(1)"), db)
        assert outcome.blocked is False
        assert [r for r in caplog.records if r.levelno >= logging.WARNING]
    finally:
        db.close()


# ── 4. 启动自检（对齐 WP-A verify_migration_head_on_startup 范式） ──────────

def test_startup_selfcheck_reports_unbindable_block_hook(isolated_data):
    """自检必须把「block 模式 hook 无法绑定实现体」报为 error 级 finding。"""
    from app.core.database import get_session
    from app.services.hook_engine import check_security_hook_bindings
    db = get_session()
    try:
        _add_hook_row(db, "some unknown block hook",
                      {"hook_point": "PreToolUse", "hook_mode": "block",
                       "hook_impl": "no_such_impl"})
        report = check_security_hook_bindings(db)
        assert any(f["level"] == "error" for f in report["findings"]), report
    finally:
        db.close()


def test_startup_selfcheck_passes_on_real_db_row_shape(isolated_data):
    """真实库行形态（缺 hook_impl）下：可绑定 ⇒ 无 error；但元数据漂移须以 warning 发声。"""
    from app.core.database import get_session
    from app.services.hook_engine import check_security_hook_bindings
    db = get_session()
    try:
        _add_hook_row(db, _SECURITY_HOOK_NAME, _REAL_DB_META)
        report = check_security_hook_bindings(db)
        assert not [f for f in report["findings"] if f["level"] == "error"], report
        assert any(f["level"] == "warning" and f["code"] == "hook_impl_metadata_missing"
                   for f in report["findings"]), report
        assert "pre_write_policy" in report["bound_security_impls"]
    finally:
        db.close()


def test_startup_selfcheck_reports_missing_security_hook(isolated_data):
    """完全没有安全 hook 行时也必须发声（D-032 写入期拦截缺位）。"""
    from app.core.database import get_session
    from app.services.hook_engine import check_security_hook_bindings
    db = get_session()
    try:
        report = check_security_hook_bindings(db)
        assert any(f["code"] == "security_hook_absent" for f in report["findings"]), report
        assert "pre_write_policy" not in report["bound_security_impls"]
    finally:
        db.close()


def test_startup_selfcheck_never_raises(isolated_data, caplog):
    """启动自检包装函数不得因异常中断启动，但必须发声（非致命 + 公理3）。"""
    from app.services.hook_engine import verify_security_hooks_on_startup
    with caplog.at_level(logging.DEBUG, logger="rebuild.hook_engine"):
        verify_security_hooks_on_startup(db=None)   # db=None → 无法自检，须发声不抛
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── 5. 真实库（非 tmp 库）断言 ─────────────────────────────────────────────

def test_real_db_security_hooks_are_all_bindable():
    """直接读真实库 backend/.data/rebuild.db（**只读**）断言其 hook 行可绑定实现体。

    这是 B-R18-3 的核心防复发断言：tmp 库跑 seed 永远带 hook_impl，绿灯掩盖了真实库漂移。
    库文件不存在时 skip（CI/干净检出场景）；存在时必须能绑定 D-032 写入期拦截。
    """
    from pathlib import Path
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.services.hook_engine import check_security_hook_bindings

    real_db = Path(__file__).resolve().parents[1] / ".data" / "rebuild.db"
    if not real_db.exists():
        pytest.skip(f"真实库不存在，跳过：{real_db}")

    # 只读打开，绝不写入真实库（不触发 create_all / 迁移 / 数据回填）
    engine = create_engine(f"sqlite:///file:{real_db}?mode=ro&uri=true")
    db = sessionmaker(bind=engine)()
    try:
        report = check_security_hook_bindings(db)
        errors = [f for f in report["findings"] if f["level"] == "error"]
        assert not errors, f"真实库安全 hook 绑定失败：{errors}"
        assert "pre_write_policy" in report["bound_security_impls"], (
            "真实库中 D-032 写入期拦截 hook 未绑定到实现体（B-R18-3 复发）")
    finally:
        db.close()
        engine.dispose()
