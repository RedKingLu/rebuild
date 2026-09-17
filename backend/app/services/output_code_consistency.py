"""P4 产出物整体一致性静态核查 —— 纯函数层（无网络、无写盘、不参与门禁）。

覆盖两条台账缺陷，它们同属"**产出物整体一致性**"这一此前无任何守卫的维度：

  · `B-V262-DUP-SYMBOL-CROSSNODE`（P1）：多个 P4 执行节点各自独立生成产物，彼此不知道
    对方已经生成过同名类。真跑实测 `MicroOA.Core/Exports/ExcelExportService.cs:40` 与
    `MicroOA.Core/Exports/MicroDTHelper.cs:40` **各实现了一份** `public sealed class
    ExcelExportService : IExcelExportService`（同命名空间、同名、同接口）⇒ C# 编译期必然
    `CS0101/CS0111`，工程无法编译。既有守卫（`_sanitize_segment` 路径合法性、
    `detect_protocol_leak` 内容合法性）都不覆盖"这个符号是否已被别的节点定义过"。

  · `B-V262-UNDECLARED-DEP`（P1）：V-04 依赖真实性校验的**第二个盲区**。V-04 查的是
    `声明 → 是否真实存在`（能抓臆造包名）；**反方向零覆盖**：`代码实际使用 → 是否已声明`。
    真跑实测产出代码 `using StackExchange.Redis;`（两处），而全部 `.csproj` 中**零声明**
    （`grep -rn "StackExchange" --include=*.csproj` = 0 命中）⇒ 编译必然报找不到类型。
    与 D-04 / Q-RW-5（声明了但依赖图冲突）方向恰好对称，二者并列才构成 V-04 的完整边界。

## 归口取舍（B-V262-DUP-SYMBOL-CROSSNODE 解除条件 ①）

台账列了两个候选方向：① P4 收口做一次产出物级符号索引与冲突报告；② 归口 P5，让构建失败
诊断能指认"重复符号"这一类别。**本模块取 ①**，理由三条：

  1. ② 的前提是"P5 真能把工程构建起来"。真跑实测的三层剥洋葱（`NU1605` 依赖还原冲突 →
     82 个 `CS1056` → 37 个 CS 错误）说明**依赖还原失败会物理遮蔽后续编译错误** ——
     这正是本缺陷此前完全不可见的原因。归口 P5 等于把检出条件绑在一个会被前置错误遮蔽的
     环节上；而 ① 是纯静态的，不依赖 restore/build 成功。
  2. 解除条件 ④ 明确禁止"以 P5 构建会失败为由关闭本条"。
  3. ① 与既有 `_run_dependency_check` 的 P4 尾部定位完全同构（产 `artifacts/p4/` 可读
     诊断 + Evidence，**不参与门禁**、不产 pass/通过 结论，R19-2-03「暴露≠拦截」），
     可复用同一套写入/Evidence 范式，不另造机制。

台账同时**否决**了"在 `_write()` 里做正则级类名提取"（多语言 / 嵌套类 / partial class
会大量误报）。本模块因此：只在 **P4 收口**跑一次全量索引（不在每次写入时判）、
**跳过 partial**、按 **namespace + 类型名** 做全限定比对、且冲突结论一律标
`needs_human_review` 而不是 `failed`。

## 能力边界（必须与结论同时呈现，否则会被误读）

  · 符号索引只覆盖 **C# (.cs) / Java (.java)** —— 这两种语言里"同一全限定类型被定义两次"
    是确定的编译错误。Python / JS / TS 允许不同模块同名，不适用本判据，**不扫**。
  · 只索引**顶层类型声明**（class/interface/struct/record/enum），不索引方法重载、
    不索引嵌套类型（嵌套类同名不构成冲突）。
  · `partial` 声明**一律跳过**（多文件拆分同一类型是 C# 的正当写法）。
  · 依赖方向只覆盖 **C# `using` / Java `import`** 与 NuGet/npm/Maven 清单；
    BCL / 框架命名空间按保守前缀清单排除（见 `FRAMEWORK_NAMESPACE_PREFIXES`）——
    **保守 = 宁可漏报，不可淹没真信号**（台账解除条件 ② 点名"内置命名空间与项目内命名空间
    不得误报"）。被排除的前缀会写进产物的 `boundary` 段，使漏报面可被读者看见。
"""

from __future__ import annotations

import re
from pathlib import Path

# ── 扫描范围 ──────────────────────────────────────────────────────────────────
_SKIP_DIR_NAMES = frozenset({".git", "node_modules", "obj", "bin", "__pycache__",
                             ".rebuild", ".vs", "packages"})
_SYMBOL_SUFFIXES = (".cs", ".java")
# 单文件体量上限：产出代码里偶发的巨型生成文件（压缩过的 js/嵌入资源）没有索引价值，
# 逐行正则却会拖慢 P4 收口。超限文件如实登记在 skipped 里，不静默丢弃。
_MAX_FILE_BYTES = 2_000_000
_MAX_FILES = 5000

# ── C# / Java 顶层类型声明 ────────────────────────────────────────────────────
# 只匹配"行首缩进 + 可选修饰符 + 关键字 + 名字"。刻意不试图解析泛型约束/基类列表 ——
# 取到名字就够，多余的解析只会增加误判面。
_TYPE_DECL_RE = re.compile(
    r"^\s*(?P<mods>(?:(?:public|internal|private|protected|static|sealed|abstract|"
    r"partial|final|readonly|ref|record)\s+)*)"
    r"(?P<kind>class|interface|struct|record|enum)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_NAMESPACE_RE = re.compile(r"^\s*(?:namespace|package)\s+([A-Za-z_][A-Za-z0-9_.]*)")
# C# using 指令：排除 `using var x = ...` / `using (` / `using static` / 别名 `using X = Y;`
_CS_USING_RE = re.compile(r"^\s*using\s+(?P<ns>[A-Za-z_][A-Za-z0-9_.]*)\s*;")
_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?(?P<ns>[A-Za-z_][A-Za-z0-9_.]*)")

# ── 框架 / BCL 命名空间前缀（保守清单，宁可漏报）──────────────────────────────
# 判据是"该前缀下的命名空间**通常**由 SDK/JDK 直接提供，不需要单独的包声明"。
# 每加一项都在放大漏报面，故只列有明确依据的：
#   System.*            → .NET BCL
#   Microsoft.CSharp / Microsoft.VisualBasic / Microsoft.Win32 → BCL 内
#   Microsoft.Extensions.* / Microsoft.AspNetCore.* → ASP.NET Core 共享框架（Sdk.Web 自带）
#   Microsoft.Net.Http.Headers → 同上
#   java.* / javax.* / jakarta.*  → JDK / Jakarta EE 平台
FRAMEWORK_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "System",
    "Microsoft.CSharp", "Microsoft.VisualBasic", "Microsoft.Win32",
    "Microsoft.Extensions", "Microsoft.AspNetCore", "Microsoft.Net.Http",
    "java", "javax", "jakarta",
)


def _iter_source_files(root: Path) -> tuple[list[Path], list[dict]]:
    """遍历 root 下的 C#/Java 源文件。返回 (文件列表, 被跳过的文件登记)。"""
    files: list[Path] = []
    skipped: list[dict] = []
    if not root.exists() or not root.is_dir():
        return files, skipped
    for p in sorted(root.rglob("*")):
        if any(part in _SKIP_DIR_NAMES for part in p.parts):
            continue
        if not p.is_file() or p.suffix.lower() not in _SYMBOL_SUFFIXES:
            continue
        if len(files) >= _MAX_FILES:
            skipped.append({"path": p.name, "reason": "file_count_cap"})
            continue
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                skipped.append({"path": _rel(p, root), "reason": "file_too_large"})
                continue
        except OSError as e:
            skipped.append({"path": _rel(p, root), "reason": f"stat_failed: {e}"})
            continue
        files.append(p)
    return files, skipped


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)


def _read_lines(p: Path) -> list[str] | None:
    try:
        return p.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _is_framework_namespace(ns: str) -> bool:
    for prefix in FRAMEWORK_NAMESPACE_PREFIXES:
        if ns == prefix or ns.startswith(prefix + "."):
            return True
    return False


# ═════════════════════════════════════════════════════════════════════════════
# ① 跨文件重复符号（B-V262-DUP-SYMBOL-CROSSNODE）
# ═════════════════════════════════════════════════════════════════════════════

# ── 花括号深度跟踪（区分顶层类型与嵌套类型的唯一手段）─────────────────────────
# 台账点名了"嵌套类会大量误报"这个误报源。仅靠行级正则**无法**区分
#   namespace N { public class Outer { public class Inner { } } }
# 里的 Outer（顶层）与 Inner（嵌套）—— 两者的行形状完全一样。故必须跟踪花括号深度：
# 声明所在深度 == 命名空间体的深度 ⇒ 顶层；更深 ⇒ 嵌套，不索引。
#
# 计数前必须先剔除注释与字符串字面量里的花括号，否则一段
#   var s = "}"; // {
# 就能让整个文件之后的深度全部错位。这里做的是**够用**的剔除（行注释 / 块注释 /
# 双引号与单引号字面量含 \ 转义），不是完整的 C# 词法分析器 —— 深度错位的后果只是
# 某个类型漏索引或多索引一次（本模块是 needs_human_review 级诊断，不参与门禁），
# 不值得为它引入一个 parser 依赖（依赖克制）。
_LINE_COMMENT_RE = re.compile(r"//.*$")
_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"' r"|'(?:\\.|[^'\\])*'")


def _strip_noise(line: str, in_block_comment: bool) -> tuple[str, bool]:
    """剔除注释与字符串字面量，返回 (净化后的行, 是否仍处于块注释中)。"""
    out = []
    i = 0
    n = len(line)
    while i < n:
        if in_block_comment:
            end = line.find("*/", i)
            if end == -1:
                return "".join(out), True
            in_block_comment = False
            i = end + 2
            continue
        start = line.find("/*", i)
        rest = line[i:] if start == -1 else line[i:start]
        rest = _LINE_COMMENT_RE.sub("", _STRING_LITERAL_RE.sub('""', rest))
        out.append(rest)
        if start == -1:
            break
        # 行注释若出现在块注释起点之前，`rest` 已被截断；此时该行剩余部分整体丢弃
        if "//" in line[i:start]:
            break
        in_block_comment = True
        i = start + 2
    return "".join(out), in_block_comment


def index_symbols(output_code_dir: Path) -> dict:
    """索引 output_code/ 下全部**顶层**类型声明，返回 {全限定名: [声明点…]} 与扫描统计。"""
    files, skipped = _iter_source_files(output_code_dir)
    index: dict[str, list[dict]] = {}
    namespaces: set[str] = set()
    unreadable: list[dict] = []
    for p in files:
        lines = _read_lines(p)
        if lines is None:
            unreadable.append({"path": _rel(p, output_code_dir), "reason": "read_failed"})
            continue
        current_ns = ""
        depth = 0
        # 尚未见到 namespace 时，顶层类型就在深度 0（文件级/无命名空间/文件作用域命名空间）
        ns_body_depth = 0
        in_block_comment = False
        for lineno, line in enumerate(lines, 1):
            clean, in_block_comment = _strip_noise(line, in_block_comment)
            if not clean.strip():
                depth += clean.count("{") - clean.count("}")
                continue
            m_ns = _NAMESPACE_RE.match(clean)
            if m_ns:
                current_ns = m_ns.group(1)
                namespaces.add(current_ns)
                # `namespace N;`（C# 文件作用域）/ `package x.y;`（Java）不开花括号 ⇒
                # 顶层类型仍在当前深度；`namespace N { … }` 则深一层。
                ns_body_depth = depth if clean.rstrip().endswith(";") else depth + 1
                depth += clean.count("{") - clean.count("}")
                continue
            m = _TYPE_DECL_RE.match(clean)
            if m and depth == ns_body_depth and "partial" not in m.group("mods"):
                # partial 是多文件拆分同一类型的正当写法 —— 不是冲突，故上面排除。
                fq = f"{current_ns}.{m.group('name')}" if current_ns else m.group("name")
                index.setdefault(fq, []).append({
                    "path": _rel(p, output_code_dir),
                    "line": lineno,
                    "kind": m.group("kind"),
                })
            depth += clean.count("{") - clean.count("}")
    return {
        "symbols": index,
        "declared_namespaces": sorted(namespaces),
        "files_scanned": len(files),
        "files_skipped": skipped + unreadable,
    }


def find_duplicate_symbols(output_code_dir: Path) -> dict:
    """检出"同一全限定类型在两个及以上**不同文件**里被声明"的冲突。

    同一文件内出现两次不计入（那是同文件内的语法问题，编译器自会报，且极可能是本模块
    的行级正则误匹配 —— 宁可漏报也不产假信号）。
    """
    idx = index_symbols(output_code_dir)
    conflicts = []
    for fq, decls in sorted(idx["symbols"].items()):
        distinct_files = sorted({d["path"] for d in decls})
        if len(distinct_files) < 2:
            continue
        conflicts.append({
            "symbol": fq,
            "kind": decls[0]["kind"],
            "file_count": len(distinct_files),
            "declarations": [d for d in decls if True],
            "statement": (f"类型 {fq} 在 {len(distinct_files)} 个不同文件中各被声明一次："
                          + "、".join(f"{d['path']}:{d['line']}" for d in decls)
                          + "。C#/Java 下同一全限定类型重复定义为编译错误"
                            "（C# CS0101/CS0111），产出工程将无法编译。"),
            # 硬拦是错的（可能是本模块误判，也可能是用户有意为之）——标"需人工复核"，
            # 与代码量观察指标同一定位（台账建议修法 ① 的原话）。
            "verdict": "needs_human_review",
        })
    return {
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "symbols_indexed": len(idx["symbols"]),
        "files_scanned": idx["files_scanned"],
        "files_skipped": idx["files_skipped"],
        "declared_namespaces": idx["declared_namespaces"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# ② 用了但未声明的依赖（B-V262-UNDECLARED-DEP）
# ═════════════════════════════════════════════════════════════════════════════

def collect_used_namespaces(output_code_dir: Path) -> dict:
    """采集产出代码实际 using / import 的外部命名空间（含每处引用坐标）。"""
    files, _skipped = _iter_source_files(output_code_dir)
    used: dict[str, list[dict]] = {}
    for p in files:
        lines = _read_lines(p)
        if lines is None:
            continue
        rx = _CS_USING_RE if p.suffix.lower() == ".cs" else _JAVA_IMPORT_RE
        for lineno, line in enumerate(lines, 1):
            m = rx.match(line)
            if not m:
                continue
            ns = m.group("ns")
            # `using static X.Y` 已由 _CS_USING_RE 排除（static 不是合法命名空间首段的
            # 一部分时匹配失败）；别名 `using X = Y;` 因结尾是 `=` 而不是 `;` 亦不匹配。
            used.setdefault(ns, []).append({"path": _rel(p, output_code_dir), "line": lineno})
    return used


def _declared_package_ids(manifests: list[dict]) -> list[str]:
    """从 manifest_parsers.parse_all_manifests() 结果里取全部已声明包 id。"""
    ids: set[str] = set()
    for m in manifests or []:
        for c in m.get("coordinates", []) or []:
            pid = (c.get("id") or "").strip()
            if pid:
                ids.add(pid)
    return sorted(ids)


def _covered_by_declared(ns: str, declared_lower: list[str]) -> str | None:
    """命名空间 ns 是否被某个已声明包覆盖；返回命中的包 id，否则 None。

    双向前缀比对（都用小写）：
      · 包 `StackExchange.Redis` 覆盖 `using StackExchange.Redis;` 与
        `using StackExchange.Redis.Extensions;`（包 id 是 ns 的前缀）；
      · 包 `Newtonsoft.Json.Bson` 也应覆盖 `using Newtonsoft.Json;`
        （ns 是包 id 的前缀 —— 声明得更细而用得更粗，同样说明该依赖已被声明）。
    """
    n = ns.lower()
    for pid in declared_lower:
        if n == pid or n.startswith(pid + ".") or pid.startswith(n + "."):
            return pid
    return None


def find_undeclared_dependencies(output_code_dir: Path, manifests: list[dict]) -> dict:
    """检出"代码用了、但依赖清单里没声明"的外部命名空间（V-04 的第二个盲区）。

    排除三类（顺序即优先级，命中即不报）：
      ① 框架/BCL 前缀（`FRAMEWORK_NAMESPACE_PREFIXES`，SDK/JDK 自带，无需单独声明）；
      ② **项目内命名空间**（产出代码自己 `namespace`/`package` 声明过的，及其子命名空间）；
      ③ 已声明包覆盖（`_covered_by_declared` 双向前缀比对）。
    """
    idx = index_symbols(output_code_dir)
    project_namespaces = idx["declared_namespaces"]
    used = collect_used_namespaces(output_code_dir)
    declared = _declared_package_ids(manifests)
    declared_lower = [d.lower() for d in declared]

    def _is_project_internal(ns: str) -> bool:
        for own in project_namespaces:
            if ns == own or ns.startswith(own + ".") or own.startswith(ns + "."):
                return True
        return False

    findings = []
    for ns in sorted(used):
        if _is_framework_namespace(ns):
            continue
        if _is_project_internal(ns):
            continue
        if _covered_by_declared(ns, declared_lower) is not None:
            continue
        refs = used[ns]
        findings.append({
            "namespace": ns,
            "reference_count": len(refs),
            "references": refs,
            "statement": (f"代码在 {len(refs)} 处 using/import 了 {ns}，但全部依赖清单中"
                          f"无任何包声明可覆盖它、且它也不是产出代码自己声明的命名空间 ⇒ "
                          f"编译必然报找不到命名空间/类型。两种常见成因：① 用到的外部包"
                          f"未在清单里声明；② 沿用了源工程的旧命名空间而迁移后已改名/未迁移。"
                          f"（已排除框架前缀与项目内命名空间）"),
            "verdict": "needs_human_review",
        })
    return {
        "findings": findings,
        "finding_count": len(findings),
        "used_namespace_count": len(used),
        "declared_package_ids": declared,
        "project_namespaces": project_namespaces,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 顶层入口
# ═════════════════════════════════════════════════════════════════════════════

def run_consistency_check(output_code_dir: Path, manifests: list[dict],
                          run_id: str = "") -> dict | None:
    """产出 `p4_output_consistency.json` 形状的完整文档。

    `output_code/` 下没有任何可索引的 C#/Java 源文件时返回 ``None``（不产噪音产物 ——
    与 `run_dependency_check` 对"无清单即不产物"的处理同一取向）。

    **不参与门禁、不产 pass/通过 结论**：本函数不读也不改 criteria_met/graph_status/status。
    """
    from datetime import datetime, timezone

    dup = find_duplicate_symbols(output_code_dir)
    if dup["files_scanned"] == 0:
        return None
    undecl = find_undeclared_dependencies(output_code_dir, manifests)

    total = dup["conflict_count"] + undecl["finding_count"]
    return {
        "artifact_type": "p4_output_consistency",
        "kind": "p4_output_consistency",
        "stage": "p4",
        "run_id": run_id,
        "schema": "p4_output_consistency_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # status 只描述"检查是否跑完"，不表达"产物是否合格"（不是门禁结论）。
        "status": "completed",
        "counts": {
            "duplicate_symbol_conflicts": dup["conflict_count"],
            "undeclared_dependencies": undecl["finding_count"],
            "total_findings": total,
            "files_scanned": dup["files_scanned"],
            "symbols_indexed": dup["symbols_indexed"],
        },
        "duplicate_symbols": dup["conflicts"],
        "undeclared_dependencies": undecl["findings"],
        "declared_package_ids": undecl["declared_package_ids"],
        "project_namespaces": undecl["project_namespaces"],
        "files_skipped": dup["files_skipped"],
        "boundary": {
            "languages_indexed": list(_SYMBOL_SUFFIXES),
            "framework_namespace_prefixes_excluded": list(FRAMEWORK_NAMESPACE_PREFIXES),
            "partial_declarations_skipped": True,
            "nested_types_indexed": False,
            "note": ("① 符号索引只覆盖 C#/Java 顶层类型（这两种语言里同一全限定类型重复定义"
                     "是确定的编译错误）；Python/JS/TS 允许不同模块同名，不适用本判据，未扫。"
                     "② 依赖方向的框架前缀排除清单是**保守**的：宁可漏报，不可用误报淹没真信号 —— "
                     "被排除前缀下若确有未声明的包（如 System.DirectoryServices.Protocols），"
                     "本检查看不见。③ 全部结论均为 needs_human_review，"
                     "**不参与 P4 门禁**（暴露≠拦截，R19-2-03）。"),
        },
        "independence_note": ("本结论来自对 output_code/ 的纯静态索引，不读取任何 P5 构建结论 —— "
                             "这是刻意的：真跑实测依赖还原失败（NU1605）会物理遮蔽后续编译错误，"
                             "把检出条件绑在构建成功上等于让本缺陷继续不可见。"),
    }
