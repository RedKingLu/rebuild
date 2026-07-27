"""D-114：写盘路径文件名清洗（治"中文描述污染文件名"，方案A Phase3）。

真跑实证的两例污染（项目 8666035c）：
  output_code/tn-009784f3/MicroDBHelper.cs（QueryExcel 方法——OleDb 读取）
  output_code/tn-64203bd5/JS、Layui 面板、服务器控件）
清洗后须为合法可编译路径，顶层白名单目录不变。
"""
from app.services.tool_registry import _sanitize_write_path, _sanitize_segment


def test_ext_then_chinese_paren_truncated():
    # 合法扩展名后的中文括注 → 截断到扩展名
    assert _sanitize_write_path(
        "output_code/tn-009784f3/MicroDBHelper.cs（QueryExcel 方法——OleDb 读取）"
    ) == "output_code/tn-009784f3/MicroDBHelper.cs"


def test_all_chinese_filename_sanitized_legal():
    # 全中文/顿号/括号文件名 → 剔为合法 ASCII（不含中文/括号/顿号）
    out = _sanitize_write_path("output_code/tn-64203bd5/JS、Layui 面板、服务器控件）")
    assert out.startswith("output_code/tn-64203bd5/")
    tail = out.split("/")[-1]
    assert all(ord(c) < 128 for c in tail)              # 无 CJK
    assert not any(ch in tail for ch in "（）、，。：")   # 无中文标点
    assert tail                                          # 非空


def test_legal_path_unchanged():
    # 合法路径原样保留
    for p in ("output_code/MicroOA.Core/Services/AuthService.cs",
              "output_code/db/schema_poc.sql",
              "patches/tn-x/foo.diff",
              "output_code/MicroOA.Core/MicroOA.Core.csproj"):
        assert _sanitize_write_path(p) == p


def test_top_dir_preserved():
    # 顶层白名单目录段不被清洗
    assert _sanitize_write_path("output_code/x.cs").startswith("output_code/")
    assert _sanitize_write_path("artifacts/p4/y.json").startswith("artifacts/")


def test_only_topdir_gets_fallback_filename():
    # 其余段全被剥空 → 补兜底文件名，不产出裸目录
    assert _sanitize_write_path("output_code/（）") == "output_code/file.txt"


def test_segment_helper():
    assert _sanitize_segment("Program.cs（入口）") == "Program.cs"
    assert _sanitize_segment("  Services  ") == "Services"
    assert _sanitize_segment("..") == ""
