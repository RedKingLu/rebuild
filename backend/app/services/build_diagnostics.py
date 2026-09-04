"""构建诊断抽取（R19-1 G1 · 修 B4 的诊断侧）。

把构建工具的 stdout/stderr 原始文本抽成**结构化诊断明细**，让真实错误码（依赖类
`NU*`、编译类 `CS*`、MSBuild 类 `MSB*`、SDK 类 `NETSDK*`）能一路走到
`artifacts/p5_validation_report.json` 与前端，而不是被截成 500 字符尾巴丢掉。

红线（Q-R19-1-8 用户裁定）：
  - **分类如实**：`MSB*` / `NETSDK*` 归 `sdk` / `msbuild`，**绝不并入 `compile`**
    冒充 CS 类明细；`NU*` 归 `dependency`。凑数即伪造（违反 D-097 与 capability-first）。
  - 诊断只来自**真实命令输出文本**，不从别的阶段结论"推断"。
  - 落盘日志过 D-032 脱敏（复用 `security_authorization.redact_secrets`）。

正则已对**真实容器输出**逐条实测（2026-09-04，mcr.microsoft.com/dotnet/sdk:8.0）：
  `/src/X.csproj : error NU1101: Unable to find package …`
  `/build/…/Broken.cs(3,30): error CS0103: … [/build/…/proj.csproj]`
  `…/Microsoft.PackageDependencyResolution.targets(266,5): error NETSDK1004: … [/x/p.csproj]`
  `MSBUILD : error MSB1003: Specify a project or solution file. …`
"""

from __future__ import annotations

import re

# 单行诊断（MSBuild / Roslyn / NuGet 通用格式）
_DIAG_RE = re.compile(
    r"^\s*(?P<file>\S[^:]*?|\S.*?)"
    r"(?:\((?P<line>\d+)(?:,(?P<col>\d+))?\))?"
    r"\s*:\s*(?P<severity>error|warning)\s+(?P<code>[A-Za-z]+[0-9]+)\s*:\s*"
    r"(?P<message>.*?)"
    r"(?:\s*\[(?P<project>[^\]]*)\]\s*)?$"
)

# 错误码前缀 → 诊断类别。**类别如实，不合并**（见文件头红线）。
_CATEGORY_BY_PREFIX = (
    ("NU", "dependency"),      # NuGet：包不存在 / 版本不可解析（NU1101 …）
    ("NETSDK", "sdk"),         # .NET SDK：资产文件缺失、目标框架不受支持 …
    ("MSB", "msbuild"),        # MSBuild：项目文件/解决方案定位、目标执行 …
    ("CS", "compile"),         # C# 编译器（Roslyn）
    ("BC", "compile"),         # VB.NET 编译器
    ("FS", "compile"),         # F# 编译器
)

# 诊断行上限（防超长日志把报告顶爆；完整原文另落盘由 evidence_refs 引用）
MAX_DIAGNOSTICS = 200
# stdout/stderr 保留尾巴长度（旧实现 500 字符导致 NU1101/CS 文本根本到不了报告）
TAIL_LIMIT = 4000


def classify_code(code: str) -> str:
    """错误码 → 诊断类别。未知前缀 → `other`（**不猜、不并入 compile**）。"""
    up = (code or "").upper()
    for prefix, category in _CATEGORY_BY_PREFIX:
        if up.startswith(prefix):
            return category
    return "other"


def parse_diagnostics(*texts: str, max_items: int = MAX_DIAGNOSTICS) -> list[dict]:
    """从命令输出抽取结构化诊断（去重、保序）。

    返回 `[{code, severity, category, file, line, column, message, project}]`。
    MSBuild 会把同一条错误在正文与摘要各打印一次 ⇒ 按 (code,file,line,col,message) 去重。
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for text in texts:
        if not text:
            continue
        for raw in text.splitlines():
            m = _DIAG_RE.match(raw)
            if not m:
                continue
            g = m.groupdict()
            code = (g.get("code") or "").upper()
            key = (code, g.get("file") or "", g.get("line") or "",
                   g.get("col") or "", (g.get("message") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "code": code,
                "severity": g.get("severity") or "",
                "category": classify_code(code),
                "file": (g.get("file") or "").strip(),
                "line": int(g["line"]) if g.get("line") else None,
                "column": int(g["col"]) if g.get("col") else None,
                "message": (g.get("message") or "").strip(),
                "project": (g.get("project") or "").strip(),
            })
            if len(out) >= max_items:
                return out
    return out


def summarize_diagnostics(diagnostics: list[dict]) -> dict:
    """按错误码与类别计数。

    `by_category` 让"拿到了什么"一目了然：`dependency>0 && compile==0` 就是
    "只有依赖类错误、没有编译类明细"的诚实事实，不给凑数留空间。
    """
    by_code: dict[str, int] = {}
    by_category: dict[str, int] = {}
    errors = 0
    warnings = 0
    for d in diagnostics:
        by_code[d["code"]] = by_code.get(d["code"], 0) + 1
        by_category[d["category"]] = by_category.get(d["category"], 0) + 1
        if d.get("severity") == "error":
            errors += 1
        elif d.get("severity") == "warning":
            warnings += 1
    return {"by_code": by_code, "by_category": by_category,
            "error_count": errors, "warning_count": warnings,
            "total": len(diagnostics)}


def tail(text: str, limit: int = TAIL_LIMIT) -> str:
    """保留输出尾巴（原始文本，过 D-032 脱敏）。"""
    from app.services.security_authorization import redact_secrets
    if not text:
        return ""
    return redact_secrets(text[-limit:])
