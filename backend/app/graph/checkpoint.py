"""Async SQLite checkpointer for the LangGraph orchestration graph (R9-5-1, T5).

Provides a process-singleton AsyncSqliteSaver bound to a dedicated sqlite file
`.data/graph_checkpoints.sqlite` — isolated from the business DB (rebuild.db) to
avoid the connection-pool coupling that caused B-DB-01 (RK-3 mitigation).

thread_id convention: thread_id == run_id (one graph thread per Run, D-085 / Q-4).

R19-4（Checkpoint 韧性）加固三层，**只加固 checkpointer 层，不另造状态机**（LangGraph 仍是
唯一主编排，D-037）：
  ① 建库前健康检查 `_ensure_healthy_checkpoint_db`（侧车孤儿 / 缺表 / 文件损坏三档处置）
  ② 单例存活探针 `_probe_saver`（"对象非 None" ≠ "可用"）
  ③ 全程超时（`_CONNECT/_SETUP/_PROBE/_CLOSE_TIMEOUT_S`）—— "删表后不挂起"由代码保证
并给 aiosqlite 连接补 `is_alive()` shim（`attach_is_alive_shim`）。

自有方案、V10 历史吸收记录（含具体文件与行号）、以及"不吸收哪些、为什么"见
`产物/草稿/R19-4-Checkpoint韧性-三步法.md`。核心取舍一句话：**修表优先、隔离兜底、
绝不静默丢数据**（V10 的"任何异常就 unlink 整库"明确不吸收）。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.config import settings

logger = logging.getLogger("rebuild.graph.checkpoint")

_CKPT_FILENAME = "graph_checkpoints.sqlite"

# AsyncSqliteSaver.setup() 建的两张表（langgraph-checkpoint-sqlite 3.1.0
# aio.py:319 / :329，均为 CREATE TABLE IF NOT EXISTS）。健康检查以"两张都在"为准
# —— V10 只查 checkpoints 一张，漏了 writes。
_REQUIRED_TABLES = ("checkpoints", "writes")

# 超时护栏。R19-4-02 的"不挂起"必须由代码保证，而不是靠"跑一遍没卡住"。
_CONNECT_TIMEOUT_S = 10.0
_SETUP_TIMEOUT_S = 10.0
_PROBE_TIMEOUT_S = 5.0
_CLOSE_TIMEOUT_S = 5.0

_conn: Optional[aiosqlite.Connection] = None
_saver: Optional[AsyncSqliteSaver] = None
_lock: Optional[asyncio.Lock] = None


class CheckpointUnavailableError(RuntimeError):
    """checkpointer 经健康检查 + 一次重建后仍不可用。

    诚实抛出而非返回 None / 降级为"无 checkpoint 也照跑"（公理 3 异常必发声、D-097）。
    """


def checkpoint_path() -> Path:
    p = settings.data_path
    p.mkdir(parents=True, exist_ok=True)
    return p / _CKPT_FILENAME


# ── is_alive shim（R19-4-01）────────────────────────────────────────────

def attach_is_alive_shim(conn) -> None:
    """给 aiosqlite 连接补 `is_alive()`（幂等，已有则不覆盖）。

    背景：aiosqlite 0.22.x 的 `Connection` 不再继承 `threading.Thread`，因此不再天然带
    `is_alive()`；`langgraph-checkpoint-sqlite` 2.0.x 会直接调用 `conn.is_alive()`。
    当前装的 3.1.0 已自带兜底（`aio.py:726-738` 在**导入时**探测类属性，取不到就回落到
    `conn._thread.is_alive()`），所以本 shim 不是"修当前崩溃"，而是：
      ① 给本模块的存活探针提供统一入口；② 对直接调用 `is_alive()` 的版本保持兼容。

    判活语义（相对 V10 `原始代码/backend/app/vnext/graph.py:259-267` 的适配）：V10 只看
    `conn._running`，但 aiosqlite 0.22.1 在 `Connection.__init__`（`core.py:85`）就把
    `_running = True`，工作线程却要到 `await conn` 才启动 —— 只看 `_running` 会把
    "线程已死但未显式 close"的连接判成活的。故这里要求 `_running` 未被置假 **且**
    工作线程存活。未知实现（两个内部属性都拿不到）时 best-effort 判活 —— 沿用 V10 的取向
    （`graph.py:265-266`）：探针宁可漏判，也不要误杀一条可用连接。
    """
    if hasattr(conn, "is_alive"):
        return

    def _is_alive() -> bool:
        if getattr(conn, "_running", None) is False:
            return False
        thread = getattr(conn, "_thread", None)
        if thread is not None and not thread.is_alive():
            return False
        return True

    conn.is_alive = _is_alive


# ── 健康检查（R19-4-01）─────────────────────────────────────────────────

def _sidecar_paths(db: Path) -> tuple[Path, Path]:
    """WAL 模式的 `-wal` / `-shm` 侧车路径。

    刻意不用 V10 的 `db.with_suffix(db.suffix + "-wal")`（`graph.py:217-218`）：
    `with_suffix` 语义是"替换后缀"，靠把旧后缀拼进新后缀来模拟"追加"，文件名无后缀时算错。
    """
    return Path(f"{db}-wal"), Path(f"{db}-shm")


def _quarantine(db: Path) -> Path:
    """把确定性损坏的库连同侧车**重命名**隔离，返回隔离后的主库路径。

    刻意"重命名"而非 V10 的 `unlink`（`graph.py:242 / :247`）—— 隔离后文件仍在，可人工取证
    或尝试修复；删除是不可逆的静默数据丢失。
    """
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = Path(f"{db}.corrupt-{ts}")
    db.rename(dest)
    for side in _sidecar_paths(db):
        if not side.exists():
            continue
        try:
            side.rename(Path(f"{side}.corrupt-{ts}"))
        except OSError:
            logger.warning("隔离侧车文件 %s 失败（不影响主库隔离结果）", side, exc_info=True)
    return dest


async def _ensure_healthy_checkpoint_db(db: Path, *, allow_quarantine: bool) -> str:
    """打开正式连接【之前】体检 checkpoint 库；返回本次处置动作（供日志与测试断言）。

    返回值：`clean_start` / `cleaned_orphan_sidecars` / `ok` / `repair_tables`
            / `quarantined_corrupt` / `corrupt_not_quarantined` / `check_inconclusive`

    三档处置，**没有任何一档会删除含数据的文件**：
      1. 主库不存在而 `-wal`/`-shm` 残留 → 上次崩溃遗留的孤儿（无主库的 WAL 无法回放，
         不含可恢复数据），清理并发声。此分支吸收自 V10 `graph.py:220-226` +
         `rebuild-archive/V10/CLAUDE.md` 的 FIND-V6c-A（侧车与主库不一致曾导致**首写挂起**）。
      2. 必需表缺失 → 只发声并返回 `repair_tables`；真正的补表交给新 saver 的 `setup()`
         （`CREATE TABLE IF NOT EXISTS`），**同库其它表的数据完整保留**。这是相对 V10
         "表缺失即 unlink 整库"（`graph.py:237-244`）的实质差异。
      3. 确定性文件级损坏 → 重命名隔离 + `error` 级发声说明"丢了什么"。

    "确定性损坏"的判据刻意收窄为：`PRAGMA quick_check` 明确非 `ok`，或抛出
    `sqlite3.DatabaseError` 但**不是** `OperationalError`。`database is locked` 属
    `OperationalError`，是瞬时状态而非损坏 —— V10 对**任何**异常一律 unlink
    （`graph.py:245-249`），会把一个只是被占用的健康库整个删掉。此行为明确不吸收。

    `allow_quarantine=False`（standalone 入口）时即便判定损坏也不动文件：该入口可能与主循环
    单例并发持有同一文件，重命名会让两边写到不同 inode（数据分叉），故只诚实发声。
    """
    db.parent.mkdir(parents=True, exist_ok=True)
    wal, shm = _sidecar_paths(db)

    if not db.exists():
        orphans = [p for p in (wal, shm) if p.exists()]
        if not orphans:
            return "clean_start"
        logger.warning(
            "checkpoint 主库 %s 不存在，但发现残留 WAL 侧车 %s —— 判为上次崩溃遗留的孤儿文件，"
            "已清理后以空库启动（无主库的 WAL 无法回放，不含可恢复数据）",
            db.name, [p.name for p in orphans])
        for p in orphans:
            p.unlink(missing_ok=True)
        return "cleaned_orphan_sidecars"

    verdict = "ok"
    missing: list[str] = []
    probe: Optional[aiosqlite.Connection] = None
    try:
        probe = await asyncio.wait_for(aiosqlite.connect(str(db)), _CONNECT_TIMEOUT_S)
        attach_is_alive_shim(probe)

        async def _inspect() -> tuple[str, set[str]]:
            async with probe.execute("PRAGMA quick_check(1)") as cur:
                row = await cur.fetchone()
            async with probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'") as cur:
                present = {r[0] for r in await cur.fetchall()}
            return (str(row[0]) if row else ""), present

        quick_check, present = await asyncio.wait_for(_inspect(), _PROBE_TIMEOUT_S)
        if quick_check.lower() != "ok":
            verdict = "corrupt"
            logger.error("checkpoint 库 %s 完整性检查未通过（quick_check=%r）", db, quick_check)
        else:
            missing = [t for t in _REQUIRED_TABLES if t not in present]
            if missing:
                verdict = "repair_tables"
    except asyncio.TimeoutError:
        verdict = "inconclusive"
        logger.warning(
            "checkpoint 库 %s 健康检查超时（>%.1fs）—— 无法判定是否损坏，"
            "【不做任何破坏性处置】，按原样打开让真实错误自然浮现", db, _PROBE_TIMEOUT_S)
    except sqlite3.OperationalError as exc:
        # 典型是 `database is locked` —— 瞬时状态，不是损坏。绝不因此毁库。
        verdict = "inconclusive"
        logger.warning(
            "checkpoint 库 %s 健康检查未能完成（%s）—— 多为被占用/加锁的瞬时状态，"
            "【不做任何破坏性处置】，按原样打开", db, exc)
    except sqlite3.DatabaseError as exc:
        verdict = "corrupt"
        logger.error("checkpoint 库 %s 判定为损坏（%s: %s）",
                     db, type(exc).__name__, exc)
    except Exception:
        verdict = "inconclusive"
        logger.warning("checkpoint 库 %s 健康检查异常 —— 不做破坏性处置，按原样打开",
                       db, exc_info=True)
    finally:
        if probe is not None:
            await _close_quietly(probe)

    if verdict == "repair_tables":
        logger.warning(
            "checkpoint 库 %s 缺少必需表 %s —— 将就地重建缺失表结构"
            "（CREATE TABLE IF NOT EXISTS），同库其它数据保留，不删库", db, missing)
        return "repair_tables"

    if verdict == "corrupt":
        if not allow_quarantine:
            logger.error(
                "checkpoint 库 %s 已损坏；本入口不是该文件的单例所有者，故【不做】破坏性处置"
                "（避免并发下重命名造成数据分叉）—— 诚实报不可用，交由应用单例入口处置", db)
            return "corrupt_not_quarantined"
        try:
            dest = _quarantine(db)
        except OSError:
            logger.error("隔离损坏的 checkpoint 库 %s 失败 —— 诚实报不可用，不强行继续",
                         db, exc_info=True)
            return "corrupt_not_quarantined"
        logger.error(
            "已隔离损坏的 checkpoint 库：%s → %s。其中的历史 checkpoint 本次【不再被读取】"
            "（文件已保留、未删除，可人工取证或尝试修复）；本次运行以空库继续，"
            "受影响的 run 需重新驱动。", db, dest)
        return "quarantined_corrupt"

    if verdict == "inconclusive":
        return "check_inconclusive"
    return "ok"


# ── 存活探针与连接管理 ──────────────────────────────────────────────────

async def _close_quietly(conn) -> None:
    """best-effort 关闭一条 aiosqlite 连接（带超时）。

    必须关：aiosqlite 的工作线程是**非 daemon** 线程（`core.py:90`），泄漏一条未关闭的连接
    就会让解释器在退出时无限 join 该线程 —— 表现为"进程跑完却不退出"。
    """
    try:
        await asyncio.wait_for(conn.close(), _CLOSE_TIMEOUT_S)
    except Exception:
        logger.warning("关闭 checkpointer 连接失败（best-effort，连接被弃用）", exc_info=True)


async def _connect(path: Path) -> aiosqlite.Connection:
    """打开一条带 `is_alive` shim 的 aiosqlite 连接（带超时）。"""
    try:
        conn = await asyncio.wait_for(aiosqlite.connect(str(path)), _CONNECT_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        raise CheckpointUnavailableError(
            f"打开 checkpoint 库超时（>{_CONNECT_TIMEOUT_S}s）：{path}") from exc
    attach_is_alive_shim(conn)
    return conn


async def _probe_saver(saver: AsyncSqliteSaver) -> bool:
    """存活探针：用一条极轻量真查询验证"连接还活着 && 必需表还在"。

    R19-4 之前 `get_checkpointer` 的错误假设是"`_saver` 非 None 就等于可用"。它既不覆盖
    连接已被关闭/线程已死，也不覆盖运行中被 `DROP TABLE`。V10 完全没有这一层
    （它只在 build 时体检一次）。
    """
    conn = getattr(saver, "conn", None)
    if conn is None:
        return False

    alive = getattr(conn, "is_alive", None)
    if callable(alive):
        try:
            if not alive():
                logger.warning("checkpointer 连接 is_alive() 为假 —— 判为不可用")
                return False
        except Exception:
            logger.debug("is_alive() 探测自身异常，转由真查询判定", exc_info=True)

    async def _query() -> None:
        async with conn.execute("SELECT 1 FROM checkpoints LIMIT 1") as cur:
            await cur.fetchone()

    try:
        await asyncio.wait_for(_query(), _PROBE_TIMEOUT_S)
        return True
    except asyncio.TimeoutError:
        logger.warning("checkpointer 存活探针超时（>%.1fs）—— 判为不可用", _PROBE_TIMEOUT_S)
        return False
    except Exception as exc:
        logger.warning("checkpointer 存活探针失败（%s: %s）—— 判为不可用",
                       type(exc).__name__, exc)
        return False


async def _discard_locked() -> None:
    """丢弃不可用的单例（必须在 `_lock` 内调用）。"""
    global _conn, _saver
    if _conn is not None:
        await _close_quietly(_conn)
    _conn = None
    _saver = None


async def _open_locked() -> AsyncSqliteSaver:
    """在 `_lock` 内打开一个经健康检查的 checkpointer（含建后复验）。"""
    global _conn, _saver
    path = checkpoint_path()
    await _ensure_healthy_checkpoint_db(path, allow_quarantine=True)

    conn = await _connect(path)
    saver = AsyncSqliteSaver(conn)
    try:
        await asyncio.wait_for(saver.setup(), _SETUP_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        await _close_quietly(conn)
        raise CheckpointUnavailableError(
            f"checkpointer 建表超时（>{_SETUP_TIMEOUT_S}s）：{path}") from exc
    except Exception:
        await _close_quietly(conn)
        raise

    # 建后复验：防"setup 没报错但表实际没建上"这类静默失败。
    if not await _probe_saver(saver):
        await _close_quietly(conn)
        raise CheckpointUnavailableError(
            f"checkpointer 建库后自检仍不可用：{path}（诚实不可用，不静默降级）")

    _conn = conn
    _saver = saver
    return saver


async def get_checkpointer() -> AsyncSqliteSaver:
    """Return the process-singleton AsyncSqliteSaver (lazy, table-initialised).

    R19-4：返回前做健康检查（不再"非 None 就无条件复用"）。探针不通过 → 发声、丢弃、
    体检、**重建一次**；重建后仍不可用 → 抛 `CheckpointUnavailableError`（不静默降级）。
    """
    global _lock
    if _saver is not None and await _probe_saver(_saver):
        return _saver
    # Lazy lock creation: bound to the CURRENT event loop.
    # Re-create if the existing lock belongs to a different loop (test isolation).
    try:
        if _lock is None:
            _lock = asyncio.Lock()
        # Quick test: try to get the running loop; if the lock's loop differs, recreate
        running = asyncio.get_running_loop()
        try:
            lock_loop = _lock._get_loop()  # type: ignore[attr-defined]
            if lock_loop is not running:
                _lock = asyncio.Lock()
        except Exception:
            _lock = asyncio.Lock()
    except RuntimeError:
        _lock = asyncio.Lock()

    async with _lock:
        # 双检：并发调用下只重建一次。
        if _saver is not None and await _probe_saver(_saver):
            return _saver
        if _saver is not None:
            logger.warning("checkpointer 单例健康检查未通过 —— 丢弃旧连接并重建（%s）",
                           checkpoint_path())
            await _discard_locked()
        return await _open_locked()


def thread_config(run_id: str) -> dict:
    """LangGraph config dict pinning the checkpoint thread to a run."""
    return {"configurable": {"thread_id": run_id}}


async def open_standalone_checkpointer() -> tuple[aiosqlite.Connection, AsyncSqliteSaver]:
    """Open a NON-singleton checkpointer bound to the CURRENT event loop.

    For background-thread graph drivers (routes_stages._run_graph_bg) that run on
    their own throwaway loop: they must NOT reuse or mutate the process-global
    singleton, which stays bound to the main/uvicorn loop and backs concurrent
    graph/state reads. Sharing one aiosqlite connection across loops (the old
    close+reopen-the-global hack) left the global singleton bound to the throwaway
    loop, which dies on asyncio.run() exit → "Cannot operate on a closed database"
    on the next main-loop read. The caller OWNS the returned connection and MUST
    close it (writes commit to the shared sqlite file, so the main-loop checkpointer
    still sees them via its own connection).

    R19-4：同样过健康检查 + `is_alive` shim + 超时，但 `allow_quarantine=False` —— 本入口
    可能与主循环单例并发持有同一文件，重命名会让两边写到不同 inode（数据分叉）。若库确实
    不可用，则诚实抛出（并已 error 发声），不静默返回半可用对象。
    """
    path = checkpoint_path()
    await _ensure_healthy_checkpoint_db(path, allow_quarantine=False)
    conn = await _connect(path)
    saver = AsyncSqliteSaver(conn)
    try:
        await asyncio.wait_for(saver.setup(), _SETUP_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        await _close_quietly(conn)
        raise CheckpointUnavailableError(
            f"checkpointer 建表超时（>{_SETUP_TIMEOUT_S}s）：{path}") from exc
    except Exception:
        # 必须关闭：否则泄漏一条非 daemon 工作线程（见 _close_quietly 注释）。
        await _close_quietly(conn)
        raise
    return conn, saver


async def close_checkpointer() -> None:
    """Close the singleton checkpointer connection (app shutdown / test teardown)."""
    global _conn, _saver, _lock
    if _conn is not None:
        try:
            await asyncio.wait_for(_conn.close(), _CLOSE_TIMEOUT_S)
        except Exception:
            # advisory：应用关闭/测试拆卸时关闭 checkpointer 连接，失败不影响后续（连接随即置空）。
            logger.debug("关闭 checkpointer 连接失败（关闭/拆卸阶段，best-effort）", exc_info=True)
    _conn = None
    _saver = None
    _lock = None  # reset so next call creates a fresh lock on the current event loop


async def reset_checkpointer_for_test() -> None:
    """Test helper: drop the singleton so a fresh temp DB can be bound."""
    await close_checkpointer()
