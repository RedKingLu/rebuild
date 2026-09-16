"""V26.3 · R24 回归锁：产出物整体一致性（跨文件重复符号 + 用了但未声明的依赖）。

  R24-06  `B-V262-DUP-SYMBOL-CROSSNODE`（P1）
          多个 P4 节点各自生成同名类，平台此前无跨节点符号级冲突检查。
          归口取舍 = P4 收口做一次产出物级符号索引与冲突报告（解除条件 ①，理由见
          `app/services/output_code_consistency` 模块 docstring）。

  R24-07  `B-V262-UNDECLARED-DEP`（P1）
          V-04 的第二个盲区：只查"声明的包是否存在"，不查"用到的包是否声明"。

本文件末尾的 `TestAgainstRealMicroOAOutput` 用**真跑留存的真实产出代码**做检出验证
（解除条件：DUP ②「在同一个真实项目上复现该场景并取得冲突被检出的实测证据」、
UNDECLARED ①②「在真实项目上取得检出证据 + 误报率经真实项目验证」）。该目录不存在时
`skip` 并在 skip 原因里写清缺了什么 —— 不静默跳过（B-ACC-BACKEND-PREREQ-SILENT-SKIP 的教训）。
"""

import json
from pathlib import Path

import pytest

from app.services import manifest_parsers
from app.services import output_code_consistency as occ


def _write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


CS_HEADER = "using System;\n\nnamespace {ns}\n{{\n"


def _cs_class(ns: str, name: str, *, modifiers="public sealed", usings=()):
    body = "".join(f"using {u};\n" for u in usings)
    return (f"using System;\n{body}\nnamespace {ns}\n{{\n"
            f"    {modifiers} class {name}\n    {{\n    }}\n}}\n")


# ══════════════════════════════════════════════════════════════════════════════
# R24-06 · 跨文件重复符号
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicateSymbols:
    def test_same_fqn_in_two_files_is_a_conflict(self, tmp_path):
        """台账原始形态：同命名空间、同类名、两个文件各一份 ⇒ C# CS0101。"""
        oc = tmp_path / "output_code"
        _write(oc, "A/ExcelExportService.cs", _cs_class("MicroOA.Exports", "ExcelExportService"))
        _write(oc, "A/MicroDTHelper.cs", _cs_class("MicroOA.Exports", "ExcelExportService"))
        out = occ.find_duplicate_symbols(oc)
        assert out["conflict_count"] == 1
        c = out["conflicts"][0]
        assert c["symbol"] == "MicroOA.Exports.ExcelExportService"
        assert c["file_count"] == 2
        assert c["verdict"] == "needs_human_review"
        assert "CS0101" in c["statement"]

    def test_same_name_in_different_namespaces_is_not_a_conflict(self, tmp_path):
        """不同命名空间同名类是**合法**的 —— 按全限定名比对，不按裸类名。"""
        oc = tmp_path / "output_code"
        _write(oc, "A/Svc.cs", _cs_class("MicroOA.Exports", "Helper"))
        _write(oc, "B/Svc.cs", _cs_class("MicroOA.Data", "Helper"))
        assert occ.find_duplicate_symbols(oc)["conflict_count"] == 0

    def test_partial_class_across_files_is_not_a_conflict(self, tmp_path):
        """台账点名的误报源之一：partial 多文件拆分是 C# 正当写法，一律跳过。"""
        oc = tmp_path / "output_code"
        _write(oc, "A/P1.cs", _cs_class("N", "Thing", modifiers="public partial"))
        _write(oc, "A/P2.cs", _cs_class("N", "Thing", modifiers="public partial"))
        assert occ.find_duplicate_symbols(oc)["conflict_count"] == 0

    def test_nested_types_are_not_indexed(self, tmp_path):
        """台账点名的误报源之二：嵌套类同名不构成冲突 ⇒ 只索引顶层声明。

        本例两个文件各有一个**不同**的顶层类，但内部嵌套类同名。不得报冲突。
        """
        oc = tmp_path / "output_code"
        _write(oc, "A/Outer1.cs",
               "namespace N\n{\n    public class Outer1\n    {\n"
               "        public class Inner { }\n    }\n}\n")
        _write(oc, "A/Outer2.cs",
               "namespace N\n{\n    public class Outer2\n    {\n"
               "        public class Inner { }\n    }\n}\n")
        out = occ.find_duplicate_symbols(oc)
        assert out["conflict_count"] == 0, out["conflicts"]

    def test_interfaces_structs_records_enums_are_indexed(self, tmp_path):
        oc = tmp_path / "output_code"
        for kind in ("interface", "struct", "record", "enum"):
            _write(oc, f"{kind}/a.cs", f"namespace N{kind}\n{{\n    public {kind} T {{ }}\n}}\n")
            _write(oc, f"{kind}/b.cs", f"namespace N{kind}\n{{\n    public {kind} T {{ }}\n}}\n")
        out = occ.find_duplicate_symbols(oc)
        assert out["conflict_count"] == 4, [c["symbol"] for c in out["conflicts"]]

    def test_python_and_js_are_not_scanned(self, tmp_path):
        """能力边界：Python/JS 允许不同模块同名，本判据不适用 ⇒ 不扫、不报。"""
        oc = tmp_path / "output_code"
        _write(oc, "a.py", "class Thing:\n    pass\n")
        _write(oc, "b.py", "class Thing:\n    pass\n")
        _write(oc, "a.ts", "export class Thing {}\n")
        out = occ.find_duplicate_symbols(oc)
        assert out["files_scanned"] == 0
        assert out["conflict_count"] == 0

    def test_build_dirs_are_skipped(self, tmp_path):
        """obj/bin 下的生成副本不得被当成"第二处声明"。"""
        oc = tmp_path / "output_code"
        _write(oc, "src/T.cs", _cs_class("N", "T"))
        _write(oc, "obj/Debug/T.cs", _cs_class("N", "T"))
        _write(oc, "bin/T.cs", _cs_class("N", "T"))
        assert occ.find_duplicate_symbols(oc)["conflict_count"] == 0

    def test_no_hard_block_verdict_is_ever_emitted(self, tmp_path):
        """解除条件：冲突标"需人工复核"而非硬拦（本模块不参与门禁）。"""
        oc = tmp_path / "output_code"
        _write(oc, "A/x.cs", _cs_class("N", "T"))
        _write(oc, "B/y.cs", _cs_class("N", "T"))
        for c in occ.find_duplicate_symbols(oc)["conflicts"]:
            assert c["verdict"] == "needs_human_review"


# ══════════════════════════════════════════════════════════════════════════════
# R24-07 · 用了但未声明的依赖
# ══════════════════════════════════════════════════════════════════════════════

def _csproj(*packages) -> str:
    refs = "".join(
        f'    <PackageReference Include="{p}" Version="1.0.0" />\n' for p in packages)
    return f'<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n{refs}  </ItemGroup>\n</Project>\n'


def _run_undeclared(oc: Path):
    return occ.find_undeclared_dependencies(oc, manifest_parsers.parse_all_manifests(oc))


class TestUndeclaredDependencies:
    def test_used_but_undeclared_package_is_reported(self, tmp_path):
        """台账原始形态：`using StackExchange.Redis;` 而全部 csproj 零声明。"""
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj("Newtonsoft.Json"))
        _write(oc, "P/Counter.cs", _cs_class("MicroOA.Services.State", "Counter",
                                             usings=("StackExchange.Redis",)))
        out = _run_undeclared(oc)
        assert [f["namespace"] for f in out["findings"]] == ["StackExchange.Redis"]
        assert out["findings"][0]["reference_count"] == 1
        assert out["findings"][0]["verdict"] == "needs_human_review"

    def test_declared_package_is_not_reported(self, tmp_path):
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj("StackExchange.Redis"))
        _write(oc, "P/C.cs", _cs_class("N", "C", usings=("StackExchange.Redis",)))
        assert _run_undeclared(oc)["findings"] == []

    def test_declared_package_covers_child_namespaces(self, tmp_path):
        """包 id 是命名空间前缀即算覆盖（`Newtonsoft.Json` 覆盖 `Newtonsoft.Json.Linq`）。"""
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj("Newtonsoft.Json"))
        _write(oc, "P/C.cs", _cs_class("N", "C", usings=("Newtonsoft.Json.Linq",)))
        assert _run_undeclared(oc)["findings"] == []

    def test_framework_namespaces_are_not_reported(self, tmp_path):
        """解除条件 ②：内置命名空间不得误报。"""
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj())
        _write(oc, "P/C.cs", _cs_class(
            "N", "C", usings=("System.Text.Json", "System.Linq",
                              "Microsoft.Extensions.DependencyInjection",
                              "Microsoft.AspNetCore.Mvc")))
        assert _run_undeclared(oc)["findings"] == []

    def test_project_internal_namespaces_are_not_reported(self, tmp_path):
        """解除条件 ②：项目内命名空间不得误报。"""
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj())
        _write(oc, "P/A.cs", _cs_class("MicroOA.Data", "A"))
        _write(oc, "P/B.cs", _cs_class("MicroOA.Web", "B", usings=("MicroOA.Data",)))
        assert _run_undeclared(oc)["findings"] == []

    def test_using_alias_and_using_var_are_not_treated_as_namespaces(self, tmp_path):
        """`using X = Y;` / `using var x = ...;` / `using (…)` 都不是命名空间引用。"""
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj())
        _write(oc, "P/C.cs",
               "using Alias = SomeVendor.Thing;\n"
               "namespace N\n{\n    public class C\n    {\n"
               "        void M()\n        {\n"
               "            using var s = Open();\n"
               "            using (var t = Open()) { }\n"
               "        }\n    }\n}\n")
        assert _run_undeclared(oc)["findings"] == []

    def test_java_imports_are_covered(self, tmp_path):
        oc = tmp_path / "output_code"
        _write(oc, "pom.xml",
               "<project><dependencies><dependency><groupId>org.ok</groupId>"
               "<artifactId>lib</artifactId><version>1.0</version></dependency>"
               "</dependencies></project>")
        _write(oc, "src/A.java",
               "package com.demo;\nimport java.util.List;\n"
               "import com.vendor.missing.Thing;\npublic class A { }\n")
        out = _run_undeclared(oc)
        # java.util 属平台前缀不报；com.vendor.missing 未声明 ⇒ 报
        assert [f["namespace"] for f in out["findings"]] == ["com.vendor.missing.Thing"]

    def test_reference_coordinates_are_recorded(self, tmp_path):
        """报出的每一项都须带可复核坐标（文件:行），不能只给结论。"""
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj())
        _write(oc, "P/C1.cs", _cs_class("N", "C1", usings=("Vendor.Lib",)))
        _write(oc, "P/C2.cs", _cs_class("N", "C2", usings=("Vendor.Lib",)))
        f = _run_undeclared(oc)["findings"][0]
        assert f["reference_count"] == 2
        assert all("path" in r and "line" in r for r in f["references"])


# ══════════════════════════════════════════════════════════════════════════════
# 顶层入口 + 非门禁纪律
# ══════════════════════════════════════════════════════════════════════════════

class TestRunConsistencyCheck:
    def test_returns_none_when_nothing_indexable(self, tmp_path):
        oc = tmp_path / "output_code"
        _write(oc, "readme.md", "hi")
        assert occ.run_consistency_check(oc, [], "run-x") is None

    def test_document_shape_and_boundary_are_present(self, tmp_path):
        oc = tmp_path / "output_code"
        _write(oc, "P/P.csproj", _csproj())
        _write(oc, "A/x.cs", _cs_class("N", "T", usings=("Vendor.Lib",)))
        _write(oc, "B/y.cs", _cs_class("N", "T"))
        doc = occ.run_consistency_check(oc, manifest_parsers.parse_all_manifests(oc), "run-y")
        assert doc["schema"] == "p4_output_consistency_v1"
        assert doc["counts"]["duplicate_symbol_conflicts"] == 1
        assert doc["counts"]["undeclared_dependencies"] == 1
        assert doc["counts"]["total_findings"] == 2
        # 能力边界必须与结论同时呈现，否则会被读成"一致性问题已全覆盖"
        b = doc["boundary"]
        assert b["partial_declarations_skipped"] is True
        assert b["nested_types_indexed"] is False
        assert "System" in b["framework_namespace_prefixes_excluded"]
        assert doc["boundary"]["note"]

    def test_status_never_expresses_a_gate_verdict(self, tmp_path):
        """非门禁纪律：`status` 只说"检查跑完了"，不说"产物合格/不合格"。"""
        oc = tmp_path / "output_code"
        _write(oc, "A/x.cs", _cs_class("N", "T"))
        _write(oc, "B/y.cs", _cs_class("N", "T"))
        doc = occ.run_consistency_check(oc, [], "run-z")
        assert doc["status"] == "completed"      # 有 1 处冲突，status 仍是 completed
        assert doc["counts"]["duplicate_symbol_conflicts"] == 1
        assert "passed" not in doc and "criteria_met" not in doc


# ══════════════════════════════════════════════════════════════════════════════
# 真实项目检出证据（DUP 解除条件②；UNDECLARED 解除条件①②）
# ══════════════════════════════════════════════════════════════════════════════

_REAL_OUTPUT = Path(
    "/home/king/rebuild/工作区/projects/3df5717c-f7f6-4e74-ae03-330c062c6c01/output_code")


class TestAgainstRealMicroOAOutput:
    """对真跑留存的**真实产出代码**跑检出，验证两条缺陷都被抓到且零误报。

    这不是夹具：`工作区/projects/3df5717c-…/output_code/` 是 V26.2 完整 MicroOA 真跑
    实际写盘的产物，台账两条缺陷的现象就是在它上面被主窗口第一手核实的。
    """

    @pytest.fixture(autouse=True)
    def _require_real_output(self):
        if not _REAL_OUTPUT.is_dir():
            pytest.skip(
                "真实项目检出证据不可用：缺少真跑留存目录 "
                f"{_REAL_OUTPUT} —— 本组用例验证的是「两条缺陷在真实产出代码上被检出且零误报」，"
                "缺该目录时本轮【未取得】真实项目证据（不等于检查通过）。"
                "恢复方式：保留/还原该 run 的 output_code/ 目录后重跑本文件。")

    def test_real_duplicate_symbol_is_detected(self):
        out = occ.find_duplicate_symbols(_REAL_OUTPUT)
        symbols = [c["symbol"] for c in out["conflicts"]]
        assert "MicroOA.Exports.ExcelExportService" in symbols, symbols
        conflict = [c for c in out["conflicts"]
                    if c["symbol"] == "MicroOA.Exports.ExcelExportService"][0]
        files = sorted(d["path"] for d in conflict["declarations"])
        assert files == ["MicroOA.Core/Exports/ExcelExportService.cs",
                         "MicroOA.Core/Exports/MicroDTHelper.cs"], files
        # 台账原文记的两处都在第 40 行 —— 逐字对得上
        assert {d["line"] for d in conflict["declarations"]} == {40}

    def test_real_undeclared_stackexchange_redis_is_detected(self):
        out = occ.find_undeclared_dependencies(
            _REAL_OUTPUT, manifest_parsers.parse_all_manifests(_REAL_OUTPUT))
        by_ns = {f["namespace"]: f for f in out["findings"]}
        assert "StackExchange.Redis" in by_ns, sorted(by_ns)
        refs = sorted(r["path"] for r in by_ns["StackExchange.Redis"]["references"])
        assert refs == [
            "MicroOA.Core/Services/State/MicroStateServiceCollectionExtensions.cs",
            "MicroOA.Core/Services/State/RedisOnlineUserCounter.cs"], refs

    def test_real_declared_packages_are_not_false_positives(self):
        """误报率验证（解除条件②）：真实项目里已声明的 5 个包一个都不得被报。"""
        out = occ.find_undeclared_dependencies(
            _REAL_OUTPUT, manifest_parsers.parse_all_manifests(_REAL_OUTPUT))
        reported = {f["namespace"] for f in out["findings"]}
        for declared in out["declared_package_ids"]:
            assert declared not in reported, declared
        # 项目自有命名空间（12 个 MicroOA.*）一个都不得被报
        for own in out["project_namespaces"]:
            assert own not in reported, own

    def test_real_findings_are_exactly_the_three_known_ones(self):
        """把本轮在真实产物上的检出结果整体钉住，使误报/漏报的变化可被观测。

        实测 3 项：① 重复符号 MicroOA.Exports.ExcelExportService；
        ② 未声明依赖 StackExchange.Redis（台账已记）；
        ③ 未声明依赖 MicroDBHelper —— **本轮新发现**：`DataAccessServiceCollectionExtensions.cs:2`
           `using MicroDBHelper;`，而产出代码里根本没有这个命名空间（源工程有，迁移后已改名为
           MicroOA.Data）⇒ 同样是必然的编译错误（CS0246），台账未记，属真阳性。
        """
        doc = occ.run_consistency_check(
            _REAL_OUTPUT, manifest_parsers.parse_all_manifests(_REAL_OUTPUT), "3df5717c")
        assert doc["counts"]["duplicate_symbol_conflicts"] == 1
        assert doc["counts"]["undeclared_dependencies"] == 2
        assert sorted(f["namespace"] for f in doc["undeclared_dependencies"]) == \
            ["MicroDBHelper", "StackExchange.Redis"]
