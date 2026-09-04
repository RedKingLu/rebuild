"""R19-4 Checkpoint 韧性 —— 健康检查 / is_alive shim / 破坏性恢复 真实测试.

验收口径（`交接/当前/V26.2-验收标准.md §3.4`）：
  R19-4-01 get_checkpointer 具备健康检查；aiosqlite 连接具备 is_alive shim
  R19-4-02 破坏性验证：删除 checkpoints 表后能自动恢复，不挂起
  R19-4-03 吸收来源标注（文档侧，见 `产物/草稿/R19-4-Checkpoint韧性-三步法.md`）

真实链路（非 mock）：真实 aiosqlite 连接 + 真实 AsyncSqliteSaver + 真实 sqlite 文件。
**所有破坏性动作只作用于本用例的临时目录**，绝不触碰真实库 `backend/.data/graph_checkpoints.sqlite`
—— 由 `ckpt_env` 夹具通过 monkeypatch `checkpoint_path` 保证（并有断言兜底）。

「不挂起」的证明方式：每个可能挂起的动作都包在 `asyncio.wait_for(..., HARD_DEADLINE)` 里。
超时即用例失败（而不是 pytest 整体卡死），故绿灯本身就是「不挂起」的证据。

asyncio_mode=auto（pyproject）。本文件自带夹具，不改 conftest.py。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite
import pytest

from app.graph import checkpoint as ckpt_mod

# 单个恢复动作的硬上限。真实健康检查的内部超时远小于此值；此处只用来把
# 「挂起」转成「用例失败」，避免测试进程被拖死。
HARD_DEADLINE = 30.0


# ── 夹具（本文件自有，不动 conftest.py）──────────────────────────────────

@pytest.fixture
def ckpt_env(tmp_path, monkeypatch):
    """把 checkpoint 文件重定向到本用例独有的 tmp 目录，并在前后清空进程单例。

    真实库保护：断言重定向后的路径确实落在 tmp_path 内，且不是 .data 下的真实文件名路径。
    """
    target = tmp_path / "graph_checkpoints.sqlite"
    monkeypatch.setattr(ckpt_mod, "checkpoint_path", lambda: target)
    assert ckpt_mod.checkpoint_path() == target
    assert str(tmp_path) in str(ckpt_mod.checkpoint_path()), "破坏性用例必须只作用于临时目录"

    asyncio.run(ckpt_mod.close_checkpointer())
    yield target
    asyncio.run(ckpt_mod.close_checkpointer())


def _sidecar_paths(db: Path) -> tuple[Path, Path]:
    return Path(str(db) + "-wal"), Path(str(db) + "-shm")


async def _table_names(db: Path) -> set[str]:
    conn = await aiosqlite.connect(str(db))
    try:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            return {r[0] for r in await cur.fetchall()}
    finally:
        await conn.close()


async def _exec(db: Path, sql: str) -> None:
    """用一条独立连接对 checkpoint 库执行 DDL/DML（模拟外部破坏）。"""
    conn = await aiosqlite.connect(str(db))
    try:
        await conn.execute(sql)
        await conn.commit()
    finally:
        await conn.close()


def _sample_checkpoint(cid: str):
    """构造 AsyncSqliteSaver.aput 可接受的最小真实 checkpoint。"""
    config = {"configurable": {"thread_id": "r19-4-thread", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "id": cid,
        "ts": "2026-09-04T00:00:00+00:00",
        "channel_values": {"probe": cid},
        "channel_versions": {"probe": "1"},
        "versions_seen": {},
    }
    return config, checkpoint


# ── R19-4-01：is_alive shim ─────────────────────────────────────────────

async def test_is_alive_shim_attached_and_tracks_real_liveness(ckpt_env):
    """aiosqlite 连接具备 is_alive()，且真实反映存活（打开=True，关闭后=False）。

    背景（吸收 V10 graph.py:252-267 的必要性判断，但换了实现）：
    aiosqlite 0.22.x 的 Connection 不再继承 threading.Thread，故不再天然带 is_alive()。
    """
    assert not hasattr(aiosqlite.Connection, "is_alive"), (
        "前提变了：aiosqlite 已自带 is_alive，shim 的兼容理由需重新评估"
    )

    conn, saver = await ckpt_mod.open_standalone_checkpointer()
    try:
        assert hasattr(conn, "is_alive"), "R19-4-01：连接必须具备 is_alive shim"
        assert conn.is_alive() is True
    finally:
        await conn.close()
    assert conn.is_alive() is False, "关闭后 is_alive 必须为 False（不能恒真）"


async def test_is_alive_shim_is_best_effort_on_unknown_impl():
    """未知实现（拿不到内部属性）时 best-effort 判活 —— 探针宁可漏判也不误杀可用连接。

    取向吸收自 V10 graph.py:265-266（AttributeError → return True）。
    """
    class _Unknown:
        pass

    obj = _Unknown()
    ckpt_mod.attach_is_alive_shim(obj)
    assert obj.is_alive() is True


async def test_get_checkpointer_conn_has_shim(ckpt_env):
    """单例路径同样打了 shim（不是只在 standalone 路径打）。"""
    saver = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    assert hasattr(saver.conn, "is_alive")
    assert saver.conn.is_alive() is True


# ── R19-4-02：破坏性验证 —— 删除 checkpoints 表后自动恢复且不挂起 ────────

async def test_drop_checkpoints_table_recovers_without_hanging(ckpt_env, caplog):
    """【R19-4-02 主用例】运行中删掉 checkpoints 表 → 下次取 checkpointer 自动恢复且可写。"""
    saver1 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    cfg, cp = _sample_checkpoint("cp-before-drop")
    await asyncio.wait_for(saver1.aput(cfg, cp, {}, {}), HARD_DEADLINE)
    assert "checkpoints" in await _table_names(ckpt_env)

    # 破坏：外部连接直接 DROP TABLE（临时库，非真实库）
    await _exec(ckpt_env, "DROP TABLE checkpoints")
    assert "checkpoints" not in await _table_names(ckpt_env)

    with caplog.at_level(logging.WARNING, logger="rebuild.graph.checkpoint"):
        saver2 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)

    # 恢复后必须真的可用（不是"返回了一个对象"就算恢复）
    cfg2, cp2 = _sample_checkpoint("cp-after-drop")
    await asyncio.wait_for(saver2.aput(cfg2, cp2, {}, {}), HARD_DEADLINE)
    got = await asyncio.wait_for(saver2.aget_tuple(cfg2), HARD_DEADLINE)
    assert got is not None and got.checkpoint["id"] == "cp-after-drop"
    assert "checkpoints" in await _table_names(ckpt_env)

    # 公理 3：必须发声，不得静默恢复
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "损坏/恢复必须记 warning 及以上（公理 3 异常必发声）"


async def test_dropped_table_repair_preserves_other_table_data(ckpt_env):
    """恢复不得静默丢数据：只丢了 checkpoints 表时，writes 表的数据必须保住。

    这是相对 V10（graph.py:237-244 表缺失即 unlink 整库）的实质差异点。
    """
    saver = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    cfg, cp = _sample_checkpoint("cp-keep")
    await asyncio.wait_for(saver.aput(cfg, cp, {}, {}), HARD_DEADLINE)
    await asyncio.wait_for(
        saver.aput_writes({"configurable": {"thread_id": "r19-4-thread",
                                            "checkpoint_ns": "",
                                            "checkpoint_id": "cp-keep"}},
                          [("probe", "kept-value")], "task-1"),
        HARD_DEADLINE)

    conn = await aiosqlite.connect(str(ckpt_env))
    try:
        async with conn.execute("SELECT COUNT(*) FROM writes") as cur:
            before = (await cur.fetchone())[0]
    finally:
        await conn.close()
    assert before > 0, "前提：writes 表应已有数据"

    await _exec(ckpt_env, "DROP TABLE checkpoints")
    saver2 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    # 恢复必须真的发生（否则下面的"数据没丢"断言会因为"什么都没做"而假绿）
    assert "checkpoints" in await _table_names(ckpt_env)
    cfg3, cp3 = _sample_checkpoint("cp-after")
    await asyncio.wait_for(saver2.aput(cfg3, cp3, {}, {}), HARD_DEADLINE)

    conn = await aiosqlite.connect(str(ckpt_env))
    try:
        async with conn.execute("SELECT COUNT(*) FROM writes") as cur:
            after = (await cur.fetchone())[0]
    finally:
        await conn.close()
    assert after == before, "修表恢复不得连带删掉同库其它数据（不得静默丢数据）"


async def test_drop_table_before_first_use_is_repaired(ckpt_env, caplog):
    """无单例在手时（进程刚起）表已缺失 → 建库前健康检查点名缺表并补回。"""
    saver = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    cfg, cp = _sample_checkpoint("cp-x")
    await asyncio.wait_for(saver.aput(cfg, cp, {}, {}), HARD_DEADLINE)
    await asyncio.wait_for(ckpt_mod.close_checkpointer(), HARD_DEADLINE)

    await _exec(ckpt_env, "DROP TABLE checkpoints")

    with caplog.at_level(logging.WARNING, logger="rebuild.graph.checkpoint"):
        saver2 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    assert "checkpoints" in await _table_names(ckpt_env)
    assert any("checkpoints" in r.getMessage() for r in caplog.records), \
        "健康检查必须点名缺失的表，而不是笼统报错"
    cfg2, cp2 = _sample_checkpoint("cp-y")
    await asyncio.wait_for(saver2.aput(cfg2, cp2, {}, {}), HARD_DEADLINE)


async def test_dead_connection_triggers_rebuild(ckpt_env, caplog):
    """存活探针：单例背后的连接被关掉 → 下次取到的是【新】saver 且可用。

    这是当前实现的核心缺口（`if _saver is not None: return _saver` 无条件复用）。
    """
    saver1 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    await saver1.conn.close()  # 背着单例把连接弄死
    assert saver1.conn.is_alive() is False

    with caplog.at_level(logging.WARNING, logger="rebuild.graph.checkpoint"):
        saver2 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    assert saver2 is not saver1, "探针必须发现死连接并重建单例"
    cfg, cp = _sample_checkpoint("cp-rebuilt")
    await asyncio.wait_for(saver2.aput(cfg, cp, {}, {}), HARD_DEADLINE)
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


async def test_healthy_singleton_is_reused(ckpt_env):
    """健康时不得反复重建（探针不能变成"每次都重开连接"）。"""
    s1 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    s2 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    s3 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    assert s1 is s2 is s3


# ── 健康检查其余分支 ────────────────────────────────────────────────────

async def test_corrupt_db_is_quarantined_not_deleted_and_voiced(ckpt_env, caplog):
    """文件级损坏 → 隔离（重命名保留）而非删除，且 error 级发声说明丢了什么。"""
    ckpt_env.write_bytes(b"not a sqlite database at all" * 100)

    with caplog.at_level(logging.WARNING, logger="rebuild.graph.checkpoint"):
        saver = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    cfg, cp = _sample_checkpoint("cp-fresh")
    await asyncio.wait_for(saver.aput(cfg, cp, {}, {}), HARD_DEADLINE)

    quarantined = list(ckpt_env.parent.glob("graph_checkpoints.sqlite.corrupt-*"))
    assert quarantined, "损坏库必须被隔离保留（不得直接 unlink 丢证据）"
    assert any(r.levelno >= logging.ERROR for r in caplog.records), \
        "丢弃历史 checkpoint 属数据影响，必须 error 级发声"


async def test_orphan_sidecars_removed_when_db_missing(ckpt_env, caplog):
    """主库不存在而 -wal/-shm 残留（崩溃遗留）→ 清理孤儿侧车并发声。

    吸收自 V10 graph.py:220-226 + CLAUDE.md FIND-V6c-A（侧车不一致导致首写挂起）。
    注意：健康检查清理完之后，`setup()` 的 `PRAGMA journal_mode=WAL` 会**重新**生成一对
    干净侧车 —— 所以断言的是"陈旧内容已消失 + 处置动作正确"，不是"侧车文件不存在"。
    """
    wal, shm = _sidecar_paths(ckpt_env)
    ckpt_env.parent.mkdir(parents=True, exist_ok=True)
    wal.write_bytes(b"stale wal bytes")
    shm.write_bytes(b"stale shm bytes")
    assert not ckpt_env.exists()

    with caplog.at_level(logging.WARNING, logger="rebuild.graph.checkpoint"):
        action = await asyncio.wait_for(
            ckpt_mod._ensure_healthy_checkpoint_db(ckpt_env, allow_quarantine=True),
            HARD_DEADLINE)

    assert action == "cleaned_orphan_sidecars"
    assert not wal.exists() and not shm.exists(), "孤儿侧车必须被清理"
    assert any(r.levelno >= logging.WARNING for r in caplog.records)

    # 清理后必须能正常起库并可写（不是"清干净了但起不来"）
    saver = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    cfg, cp = _sample_checkpoint("cp-after-sidecar-cleanup")
    await asyncio.wait_for(saver.aput(cfg, cp, {}, {}), HARD_DEADLINE)
    assert b"stale" not in wal.read_bytes(), "新侧车不应残留陈旧内容"


async def test_locked_db_is_not_destroyed(ckpt_env, caplog):
    """瞬时不可判（被独占锁占用）→ 只发声，绝不隔离/删除健康库。

    这是【不吸收】V10 graph.py:245-249（任何异常都 unlink）的直接回归防护。
    """
    # 先建好一个健康库
    await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)
    await asyncio.wait_for(ckpt_mod.close_checkpointer(), HARD_DEADLINE)
    size_before = ckpt_env.stat().st_size

    # 用另一条连接开排他事务，令体检查询拿不到锁
    blocker = await aiosqlite.connect(str(ckpt_env))
    try:
        await blocker.execute("PRAGMA busy_timeout=0")
        await blocker.execute("BEGIN EXCLUSIVE")
        with caplog.at_level(logging.WARNING, logger="rebuild.graph.checkpoint"):
            try:
                await asyncio.wait_for(
                    ckpt_mod._ensure_healthy_checkpoint_db(ckpt_env, allow_quarantine=True),
                    HARD_DEADLINE)
            except Exception:
                pass  # 体检自身失败是允许的；不允许的是把库删/隔离掉
    finally:
        try:
            await blocker.execute("ROLLBACK")
        except Exception:
            pass
        await blocker.close()

    assert ckpt_env.exists(), "被锁占用不等于损坏 —— 健康库不得被删除"
    assert ckpt_env.stat().st_size == size_before
    assert not list(ckpt_env.parent.glob("*.corrupt-*")), "瞬时锁不得触发隔离"


async def test_standalone_opener_never_quarantines(ckpt_env, caplog):
    """standalone 入口（后台图线程/恢复扫描）可能与单例并发持有同一文件 →
    只发声不做破坏性隔离，避免两边写到不同 inode 造成数据分叉。"""
    ckpt_env.write_bytes(b"garbage not sqlite" * 100)
    with caplog.at_level(logging.WARNING, logger="rebuild.graph.checkpoint"):
        with pytest.raises(Exception):
            conn, saver = await asyncio.wait_for(
                ckpt_mod.open_standalone_checkpointer(), HARD_DEADLINE)
    assert not list(ckpt_env.parent.glob("*.corrupt-*")), \
        "standalone 入口不得隔离文件（allow_quarantine=False）"
    assert any(r.levelno >= logging.ERROR for r in caplog.records), \
        "不可用必须诚实发声，不得静默"


# ── 恢复必须对生产调用方真实生效（FlowRuntime 联动）────────────────────

async def test_flow_runtime_recompiles_after_checkpointer_rebuild(ckpt_env):
    """checkpointer 被重建后，FlowRuntime 必须换用新 saver 重新编译。

    否则编译图一直握着已死的 saver —— checkpoint 层"恢复"了，生产调用方仍然不可用（假恢复）。
    """
    from app.graph.runtime import FlowRuntime

    rt = FlowRuntime()
    g1 = await asyncio.wait_for(rt._graph(), HARD_DEADLINE)
    s1 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)

    await s1.conn.close()  # 弄死当前 saver 的连接
    g2 = await asyncio.wait_for(rt._graph(), HARD_DEADLINE)
    s2 = await asyncio.wait_for(ckpt_mod.get_checkpointer(), HARD_DEADLINE)

    assert s2 is not s1
    assert g2 is not g1, "saver 换了之后编译图必须重建，不能继续用死连接"
    snap = await asyncio.wait_for(rt.get_state("no-such-run"), HARD_DEADLINE)
    assert snap is not None  # 能真正读状态即证明恢复对生产路径生效


# ── 超时护栏存在性（"不挂起"由代码保证，不靠运气）──────────────────────

def test_timeout_guards_are_configured():
    """所有可能阻塞的动作都有命名超时常量（不挂起的硬保障，V10 缺失）。"""
    for name in ("_CONNECT_TIMEOUT_S", "_SETUP_TIMEOUT_S", "_PROBE_TIMEOUT_S"):
        v = getattr(ckpt_mod, name, None)
        assert isinstance(v, (int, float)) and v > 0, f"缺少超时常量 {name}"
