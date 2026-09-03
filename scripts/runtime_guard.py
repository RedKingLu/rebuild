#!/usr/bin/env python3
"""运行时数据守护 —— 防止 git 操作静默摧毁被忽略的运行物。

## 为什么需要它

`backend/.data/rebuild.db` 与 `backend/.data/graph_checkpoints.sqlite` 处于一种危险组合：

1. **历史上曾被 git 跟踪**（直到 Q-R17.2-1 才 `git rm --cached`）；
2. **当前被 `.gitignore` 忽略**（`backend/.gitignore:1` 的 `.data/`）。

git 对「未跟踪且未忽略」的文件有保护（checkout 会报错拒绝覆盖），但对
**已忽略**的文件**不加警告直接覆盖/删除**。因此任何切换到旧提交的操作
（`git checkout <旧提交>`、`git merge --ff-only`、`git reset --hard`、rebase…）
都可能把真实运行库替换成历史版本、或在切回时把它删掉。

2026-09-03 已实测发生过一次：`checkout master` + FF 删除了 12.5MB 的
`rebuild.db`（见 `证据/进度追踪/02-阻塞项.md` 的
B-MIGRATE-GITIGNORED-DB-CLOBBER）。

## 机制

- `snapshot`：把受保护文件快照到**仓库之外**的目录，滚动保留最近 N 份。
  快照放仓库外是刻意的 —— 放在仓库内即便被 gitignore，也会被
  `git clean -xdf` 一并删除。
- `check`：体检。文件缺失 / sqlite 完整性失败 / 体积相对最新快照异常缩小
  （典型症状：被历史小版本覆盖），即判定异常；`--auto-restore` 时自动回滚。
- sqlite 快照走 **sqlite3 备份 API**（而非 `cp`），得到单文件一致快照，
  不依赖 `-wal`/`-shm`；回滚时会清掉陈旧的 `-wal`/`-shm` 以免不一致。

## 用法

    python3 scripts/runtime_guard.py snapshot        # 立即快照
    python3 scripts/runtime_guard.py check           # 只体检，不改动
    python3 scripts/runtime_guard.py check --auto-restore   # 体检并自动回滚
    python3 scripts/runtime_guard.py status          # 看快照与当前状态

## 装成 git 钩子（推荐，一次性）

    python3 scripts/runtime_guard.py install

**为什么必须用 `install` 而不是 `git config core.hooksPath .githooks`**：
`core.hooksPath` 指向**工作树内**的目录，而 `.githooks/` 与本脚本在**早于它们被加入
的历史提交里并不存在** —— 一旦 `checkout` 到那种旧提交，守护自身先消失，钩子静默
失效，而这恰恰是最危险的场景（2026-09-03 演练实测：`checkout a759fe9` 时钩子未触发，
库被换成 132KB 历史版本）。

`install` 把钩子与脚本副本装进 **`.git/`**（`.git/hooks/` 与
`.git/rebuild-runtime-guard.py`）。`.git/` 不属于工作树，**不受 checkout 影响，也不被
`git clean -xdf` 清除**，因此在任何提交上都始终生效。

`.githooks/` 随仓库提交，仅作为**模板**供 `install` 复制，本身不被 git 自动启用 ——
对外部贡献者是安全默认，钩子不会不请自来。

快照目录可用 `REBUILD_SNAPSHOT_DIR` 覆盖，默认 `~/rebuild-runtime-snapshots`。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 受保护的运行物（相对仓库根）。判据：曾被 git 跟踪过 **且** 当前被 gitignore 忽略。
PROTECTED = (
    "backend/.data/rebuild.db",
    "backend/.data/graph_checkpoints.sqlite",
    "community/backend/.data/community.db",
)

KEEP_SNAPSHOTS = 5
# 体积相对最新快照缩水超过此比例即视为可疑（被历史小版本覆盖的典型症状）
SHRINK_ALERT_RATIO = 0.5
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def snapshot_root() -> Path:
    env = os.environ.get("REBUILD_SNAPSHOT_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "rebuild-runtime-snapshots"


def is_sqlite(path: Path) -> bool:
    return path.suffix in SQLITE_SUFFIXES


def sqlite_ok(path: Path) -> tuple[bool, str]:
    """完整性体检。返回 (是否健康, 说明)。"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                return False, f"integrity_check={result}"
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            return True, f"integrity=ok tables={tables}"
        finally:
            conn.close()
    except Exception as exc:  # 打不开也算不健康 —— 公理3：异常必发声
        return False, f"{type(exc).__name__}: {exc}"


def copy_sqlite(src: Path, dst: Path) -> None:
    """用 sqlite3 备份 API 产出一致快照（含已提交的 WAL 内容）。"""
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        dst_conn = sqlite3.connect(str(dst), timeout=30)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def snapshots() -> list[Path]:
    root = snapshot_root()
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)


def newest_snapshot_of(rel: str) -> Path | None:
    """最新的、确实包含该文件的快照中的对应文件。"""
    for snap in reversed(snapshots()):
        candidate = snap / rel.replace("/", "__")
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def cmd_snapshot(args: argparse.Namespace) -> int:
    root, dest_root = repo_root(), snapshot_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_root / stamp
    manifest: dict[str, object] = {"created": stamp, "files": {}}
    saved = 0

    for rel in PROTECTED:
        src = root / rel
        if not src.is_file():
            continue
        healthy, detail = (True, "non-sqlite")
        if is_sqlite(src):
            healthy, detail = sqlite_ok(src)
        if args.if_healthy and not healthy:
            print(f"  跳过（当前不健康，不用坏数据覆盖好快照）: {rel} — {detail}")
            continue

        dest.mkdir(parents=True, exist_ok=True)
        flat = dest / rel.replace("/", "__")
        if is_sqlite(src):
            copy_sqlite(src, flat)
        else:
            shutil.copy2(src, flat)
        manifest["files"][rel] = {
            "bytes": flat.stat().st_size,
            "source_bytes": src.stat().st_size,
            "health": detail,
        }
        saved += 1
        print(f"  已快照 {rel} → {flat.name} ({flat.stat().st_size // 1024} KB, {detail})")

    if saved == 0:
        print("没有可快照的文件（受保护路径均不存在或不健康）")
        return 0

    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 滚动清理
    existing = snapshots()
    for old in existing[:-KEEP_SNAPSHOTS] if len(existing) > KEEP_SNAPSHOTS else []:
        shutil.rmtree(old, ignore_errors=True)
        print(f"  清理旧快照 {old.name}")

    print(f"快照完成: {dest}")
    return 0


def is_tracked(rel: str) -> bool:
    """该路径是否被【当前提交/索引】跟踪。

    切到旧提交时，这些路径可能重新变成「被 git 跟踪」——此时工作区副本由 git 管理
    （内容是历史版本）。这种情况**不能覆盖**：覆盖会造成脏工作区，反过来挡住
    后续分支切换（2026-09-03 演练实测：切回 master 被 git 拒绝）。
    """
    root = repo_root()
    return (
        os.system(f'git -C "{root}" ls-files --error-unmatch -- "{rel}" >/dev/null 2>&1')
        == 0
    )


def cmd_check(args: argparse.Namespace) -> int:
    root = repo_root()
    problems: list[str] = []

    for rel in PROTECTED:
        target = root / rel
        backup = newest_snapshot_of(rel)

        # 该文件从来没被快照过、且当前也不存在 —— 说明本机本就没有它，不算问题
        if not target.is_file() and backup is None:
            continue

        # 当前提交跟踪该路径 ⇒ 交给 git 管，不介入（否则脏工作区会挡住分支切换）
        if is_tracked(rel):
            where = backup.parent.name if backup else "无快照"
            print(
                f"  ⚠ {rel}: 当前提交跟踪该路径，工作区副本为历史版本，"
                f"不覆盖。真实数据保留在快照 {where}，切回不跟踪该路径的提交后会自动回滚"
            )
            continue

        reason = None
        if not target.is_file():
            reason = "文件缺失"
        elif is_sqlite(target):
            healthy, detail = sqlite_ok(target)
            if not healthy:
                reason = f"sqlite 异常（{detail}）"
        if reason is None and backup is not None and target.is_file():
            cur, ref = target.stat().st_size, backup.stat().st_size
            if ref > 0 and cur < ref * SHRINK_ALERT_RATIO:
                reason = (
                    f"体积异常缩小 {cur // 1024}KB < {ref // 1024}KB×"
                    f"{SHRINK_ALERT_RATIO}（疑被历史版本覆盖）"
                )

        if reason is None:
            print(f"  ✓ {rel}")
            continue

        problems.append(f"{rel}: {reason}")
        print(f"  ✗ {rel}: {reason}")

        if not args.auto_restore:
            continue
        if backup is None:
            print(f"    ! 无可用快照，无法自动回滚 —— 请从导出包或备份手工恢复")
            continue

        # 可疑文件不删，挪走留证
        if target.is_file():
            aside = target.with_name(f"{target.name}.suspect-{int(time.time())}")
            target.rename(aside)
            print(f"    可疑文件已挪至 {aside.name}（未删除）")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        # 陈旧的 WAL/SHM 与新回滚的主库不匹配，必须清掉
        for suffix in ("-wal", "-shm"):
            stale = target.with_name(target.name + suffix)
            if stale.exists():
                stale.unlink()
                print(f"    清理陈旧 {stale.name}")
        ok, detail = (True, "non-sqlite")
        if is_sqlite(target):
            ok, detail = sqlite_ok(target)
        print(f"    已从快照 {backup.parent.name} 回滚 → {'健康 ✓' if ok else '仍异常 ✗'} {detail}")

    if problems and not args.auto_restore:
        print(f"\n发现 {len(problems)} 处异常。加 --auto-restore 可自动回滚。")
        return 1
    if not problems:
        print("运行时数据健康。")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    root = repo_root()
    print(f"仓库:     {root}")
    print(f"快照目录: {snapshot_root()}  （刻意置于仓库外，避免被 git clean -xdf 清掉）")
    snaps = snapshots()
    print(f"\n快照 {len(snaps)} 份（保留上限 {KEEP_SNAPSHOTS}）:")
    for snap in snaps:
        size = sum(f.stat().st_size for f in snap.rglob("*") if f.is_file())
        print(f"  {snap.name}  {size // 1024 // 1024} MB")
    print("\n当前受保护文件:")
    for rel in PROTECTED:
        target = root / rel
        if not target.is_file():
            print(f"  - {rel}: 不存在")
            continue
        detail = sqlite_ok(target)[1] if is_sqlite(target) else "non-sqlite"
        print(f"  - {rel}: {target.stat().st_size // 1024} KB, {detail}")
    git_dir = Path(os.popen(f"git -C {root} rev-parse --absolute-git-dir").read().strip())
    installed = (git_dir / "rebuild-runtime-guard.py").is_file()
    hooks_ok = [h for h in RESTORE_HOOKS + SNAPSHOT_HOOKS if (git_dir / "hooks" / h).is_file()]
    override = os.popen(f"git -C {root} config --local core.hooksPath").read().strip()
    print(f"\n守护安装状态: {'已安装 ✓' if installed else '未安装 —— 执行 python3 scripts/runtime_guard.py install'}")
    print(f"  .git/hooks 中已就位: {', '.join(hooks_ok) if hooks_ok else '(无)'}")
    if override:
        print(f"  ⚠️ core.hooksPath={override} 会覆盖 .git/hooks 使安装失效 —— 请 unset")
    return 0


RESTORE_HOOKS = ("post-checkout", "post-merge", "post-rewrite")
SNAPSHOT_HOOKS = ("pre-commit",)

_HOOK_TEMPLATE = """#!/bin/sh
# rebuild 运行时数据守护（由 scripts/runtime_guard.py install 生成，勿手改）。
#
# 刻意安装在 .git/hooks/ 而非 core.hooksPath=.githooks：.git/ 不属于工作树，
# 不受 checkout 影响、也不被 git clean -xdf 清除，因此切到【早于守护被引入】的
# 旧提交时依然生效 —— 而那正是最危险的场景。
GUARD="$(git rev-parse --git-dir)/rebuild-runtime-guard.py"
[ -f "$GUARD" ] || exit 0
python3 "$GUARD" {action} || true
exit 0
"""


def cmd_install(_args: argparse.Namespace) -> int:
    root = repo_root()
    git_dir = Path(
        os.popen(f"git -C {root} rev-parse --absolute-git-dir").read().strip()
    )
    if not git_dir.is_dir():
        print("✗ 找不到 .git 目录，无法安装")
        return 1

    # 脚本副本放进 .git/ —— 与钩子同样免受 checkout / git clean 影响
    guard_copy = git_dir / "rebuild-runtime-guard.py"
    shutil.copy2(Path(__file__).resolve(), guard_copy)
    guard_copy.chmod(0o755)
    print(f"  已安装守护脚本副本 → {guard_copy}")

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in RESTORE_HOOKS:
        path = hooks_dir / name
        path.write_text(_HOOK_TEMPLATE.format(action="check --auto-restore"), encoding="utf-8")
        path.chmod(0o755)
        print(f"  已安装钩子 {name}（check --auto-restore）")
    for name in SNAPSHOT_HOOKS:
        path = hooks_dir / name
        path.write_text(
            _HOOK_TEMPLATE.format(action="snapshot --if-healthy >/dev/null 2>&1"),
            encoding="utf-8",
        )
        path.chmod(0o755)
        print(f"  已安装钩子 {name}（snapshot --if-healthy）")

    # core.hooksPath 若被设过会【覆盖】.git/hooks，必须清掉，否则本次安装无效
    current = os.popen(f"git -C {root} config --local core.hooksPath").read().strip()
    if current:
        os.system(f"git -C {root} config --local --unset core.hooksPath")
        print(f"  已清除 core.hooksPath（原值 {current}）—— 它会覆盖 .git/hooks 使安装失效")

    print("安装完成。先建一份基线快照：python3 scripts/runtime_guard.py snapshot")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="运行时数据守护")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="快照受保护的运行物")
    p_snap.add_argument(
        "--if-healthy",
        action="store_true",
        help="仅在文件健康时快照（避免用坏数据覆盖好快照）",
    )
    p_snap.set_defaults(func=cmd_snapshot)

    p_check = sub.add_parser("check", help="体检，可自动回滚")
    p_check.add_argument("--auto-restore", action="store_true", help="发现异常即从最新快照回滚")
    p_check.set_defaults(func=cmd_check)

    sub.add_parser("status", help="查看快照与当前状态").set_defaults(func=cmd_status)
    sub.add_parser(
        "install", help="把钩子与脚本副本装进 .git/（免受 checkout 影响）"
    ).set_defaults(func=cmd_install)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
