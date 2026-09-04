#!/usr/bin/env python3
"""R19-4-02 破坏性验证 —— 删除 checkpoints 表后自动恢复且不挂起。

## 安全边界（务必看清）

本脚本**绝不触碰真实库** `backend/.data/graph_checkpoints.sqlite`：

1. 用 **sqlite3 备份 API** 把真实库读出一份**一致副本**到 `/tmp/r19-4-destructive-<pid>/`
   （只读真实库，不写、不删、不改；不依赖也不动 `-wal`/`-shm`）。
2. 把 `settings.data_dir` 重定向到那个临时目录，之后**所有破坏与恢复都发生在副本上**。
3. 前后各算一次真实库的 sha256 + size + mtime，**逐字段比对**并打印 —— 用真实库未被改动
   这件事本身作为证据，而不是靠"我没写它"的口头保证。

## 「不挂起」怎么证明

每一步都包在 `asyncio.wait_for(..., STEP_DEADLINE)` 里，并打印真实耗时。
超时 = 脚本非零退出并报 HANG；正常退出且耗时远小于 deadline = 不挂起。
外层再由调用方 `timeout(1)` 兜底（见 04-施工记录 中的命令行）。

用法（在 backend/ 下）：
    ./.venv/bin/python ../scripts/r19_4_destructive_check.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

STEP_DEADLINE = 20.0

REPO = Path(__file__).resolve().parent.parent
REAL_DB = REPO / "backend" / ".data" / "graph_checkpoints.sqlite"

sys.path.insert(0, str(REPO / "backend"))


def fingerprint(p: Path) -> dict:
    if not p.exists():
        return {"exists": False}
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    st = p.stat()
    return {"exists": True, "sha256": h.hexdigest(), "size": st.st_size,
            "mtime_ns": st.st_mtime_ns}


def consistent_copy(src: Path, dst: Path) -> None:
    """用 sqlite3 备份 API 做一致副本（只读 src）。"""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def table_names(db: Path) -> set[str]:
    c = sqlite3.connect(str(db))
    try:
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()


def row_count(db: Path, table: str) -> int:
    c = sqlite3.connect(str(db))
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


async def timed(label: str, coro):
    t0 = time.monotonic()
    try:
        out = await asyncio.wait_for(coro, STEP_DEADLINE)
    except asyncio.TimeoutError:
        print(f"  ✗ HANG: {label} 超过 {STEP_DEADLINE}s 未返回")
        raise SystemExit(2)
    dt = time.monotonic() - t0
    print(f"  ✓ {label}（{dt * 1000:.1f} ms）")
    return out


async def main() -> int:
    import uuid
    print("=" * 78)
    print("R19-4-02 破坏性验证：删除 checkpoints 表后能自动恢复，不挂起")
    print("=" * 78)

    before = fingerprint(REAL_DB)
    print(f"\n[0] 真实库指纹（验证前）  {REAL_DB}")
    print(f"    size={before.get('size')}  sha256={str(before.get('sha256'))[:16]}…")

    work = Path(tempfile.mkdtemp(prefix=f"r19-4-destructive-{os.getpid()}-"))
    copy_db = work / "graph_checkpoints.sqlite"
    print(f"\n[1] 复制真实库到临时目录（sqlite backup API，只读真实库）")
    print(f"    → {copy_db}")
    consistent_copy(REAL_DB, copy_db)
    print(f"    副本表: {sorted(table_names(copy_db))}")
    print(f"    副本 checkpoints 行数: {row_count(copy_db, 'checkpoints')}")
    print(f"    副本 writes 行数:      {row_count(copy_db, 'writes')}")

    # 重定向到副本目录 —— 此后所有操作只作用于副本
    import app.core.config as cfg
    object.__setattr__(cfg.settings, "data_dir", str(work))
    from app.graph import checkpoint as ckpt

    assert ckpt.checkpoint_path() == copy_db, ckpt.checkpoint_path()
    print(f"\n[2] checkpoint_path() 已重定向 → {ckpt.checkpoint_path()}")

    saver = await timed("首次 get_checkpointer()", ckpt.get_checkpointer())
    assert hasattr(saver.conn, "is_alive") and saver.conn.is_alive() is True
    print("    R19-4-01：连接具备 is_alive shim 且为 True")

    # checkpoint id 用真实 UUID：LangGraph 的 prepare_next_tasks 会 unhexlify 该 id，
    # 随手编的字符串会让读状态在【测试脚本自己的构造】上炸，与被验证的恢复能力无关。
    id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
    cfg_a = {"configurable": {"thread_id": "r19-4-destructive", "checkpoint_ns": ""}}
    cp_a = {"v": 1, "id": id_a, "ts": "2026-09-04T00:00:00+00:00",
            "channel_values": {"probe": "before"}, "channel_versions": {"probe": "1"},
            "versions_seen": {}}
    await timed("写入验证前 checkpoint", saver.aput(cfg_a, cp_a, {}, {}))

    writes_before = row_count(copy_db, "writes")
    ck_before = row_count(copy_db, "checkpoints")
    print(f"\n[3] 破坏：对【副本】执行 DROP TABLE checkpoints"
          f"（丢弃前 checkpoints={ck_before} 行，writes={writes_before} 行）")
    c = sqlite3.connect(str(copy_db))
    try:
        c.execute("DROP TABLE checkpoints")
        c.commit()
    finally:
        c.close()
    assert "checkpoints" not in table_names(copy_db)
    print("    已确认 checkpoints 表消失")

    print("\n[4] 自动恢复（每步带 wait_for，超时即报 HANG）")
    saver2 = await timed("破坏后 get_checkpointer()", ckpt.get_checkpointer())
    cfg_b = {"configurable": {"thread_id": "r19-4-destructive", "checkpoint_ns": ""}}
    cp_b = {"v": 1, "id": id_b, "ts": "2026-09-04T00:00:01+00:00",
            "channel_values": {"probe": "after"}, "channel_versions": {"probe": "2"},
            "versions_seen": {}}
    await timed("恢复后写入 checkpoint", saver2.aput(cfg_b, cp_b, {}, {}))
    got = await timed("恢复后读回 checkpoint", saver2.aget_tuple(cfg_b))
    assert got is not None and got.checkpoint["id"] == id_b, got

    # 恢复对生产调用方（LangGraph 主编排入口）真实生效：走真实编译图读状态。
    # 用一个未写过 checkpoint 的 thread_id —— 要验的是"编译图换用了新 saver 且能查库"，
    # 不是本脚本手工构造的 checkpoint 能否被 Pregel 完整解读。
    from app.graph.runtime import FlowRuntime
    rt = FlowRuntime()
    snap = await timed("FlowRuntime.get_state() 走真实编译图",
                       rt.get_state("r19-4-untouched-thread"))
    assert snap is not None

    writes_after = row_count(copy_db, "writes")
    print(f"\n[5] 恢复结果")
    print(f"    表:                  {sorted(table_names(copy_db))}")
    print(f"    writes 行数 前/后:   {writes_before} / {writes_after}"
          f"  → {'保住（未连带删库）' if writes_after == writes_before else '❌ 丢了'}")
    print(f"    隔离文件:            "
          f"{[p.name for p in work.glob('*.corrupt-*')] or '无（本场景是缺表，就地修表即可）'}")

    await ckpt.close_checkpointer()

    after = fingerprint(REAL_DB)
    print(f"\n[6] 真实库指纹（验证后）")
    print(f"    size={after.get('size')}  sha256={str(after.get('sha256'))[:16]}…")
    same = before == after
    print(f"    与验证前逐字段一致（sha256/size/mtime_ns）：{'是' if same else '❌ 否'}")

    shutil.rmtree(work, ignore_errors=True)

    ok = (writes_after == writes_before) and same
    print("\n" + "=" * 78)
    print(f"结论：{'通过' if ok else '不通过'}"
          f" —— 删表后自动恢复、可读可写、不挂起、writes 未丢、真实库零改动")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    async def _guarded() -> int:
        try:
            return await main()
        finally:
            # 必须关：aiosqlite 的工作线程非 daemon，泄漏一条连接会让解释器退出时无限
            # join 该线程（表现为"跑完却不退出"）。异常路径尤其要关。
            from app.graph import checkpoint as _ckpt
            await _ckpt.close_checkpointer()

    raise SystemExit(asyncio.run(_guarded()))
