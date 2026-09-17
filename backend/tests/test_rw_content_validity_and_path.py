"""D-02 + D-P0-01：写盘路径文件名清洗 与 写盘前内容合法性硬拦。

事实源：`产物/草稿/V26.2-总验收真跑-问题梳理与返工修复计划.md` §3（D-P0-01 / D-02）+ §4 批次 A/B。
证据：真实规模真跑（项目 `3df5717c-…` / run `run-eab45b`，1018 源文件 / 197,447 行）。

本文件锁三件事：
  1. D-02：文件名主干的**前导** `_` 必须原样保留（Razor `_ViewStart` / Python `__init__` 等强
     约定名，改名即失效），同时 D-114"中文描述污染文件名"的既有清洗能力**不得**削弱。
  2. D-P0-01 正向：模型工具调用协议原文（真跑落盘的 6 个真实样本，逐字取自 `工作区/`）
     必须被**拒写**并 `logger.warning` 发声，且文件**不落盘**。
  3. D-P0-01 防误伤：正常代码里的 `invoke` / `parameter` / `calls` 等英文单词、`<parameter
     name=…>` XML 节点、中文文档的全角竖线分隔写法，**一律放行**。

用 conftest 的 autouse `isolated_data` 夹具（临时 DB + 临时 workspace），不触碰真实 `工作区/`。
"""

import logging

from app.services import tool_registry
from app.services.content_validity import (
    PROTOCOL_LEAK_REASON_CODE,
    detect_protocol_leak,
)
from app.services.tool_registry import _sanitize_segment, _sanitize_write_path
from app.services.workspace_service import init_workspace, workspace_path

_PID = "proj-rw-content-validity"

_TOOL_LOGGER = "rebuild.tool_registry"


def _init():
    init_workspace(_PID)
    return workspace_path(_PID)


# ── 真跑落盘的协议泄漏样本（逐字取自 `工作区/`，只读证据，勿改）──────────────
# 取证方式：`grep -rl 'DSML' 工作区/projects/*/output_code`
# 覆盖到的标记变体：`｜｜DSML｜｜ calls` / `｜｜DSML｜｜tool_calls` / 带空格与不带空格的
# `invoke` / `parameter`，以及对应的闭合 `</…>` 形态。
DSML_LEAK_SAMPLES = {
    # 真实产出文件：Views/Default.aspx
    'Views/Default.aspx': (
        "<｜｜DSML｜｜ calls>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">output_code/MicroOA.Core/Infrastructure/Hosting/FriendlyUrlMiddleware.cs</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">output_code/MicroOA.Core/Services/State/MicroSessionExtensions.cs</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">output_code/MicroOA.Core/Services/State/MicroSiteInfo.cs</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">output_code/MicroOA.Core/Services/State/MicroUserSession.cs</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    ),
    # 真实产出文件：Services/Mail/MicroPublicHelper.cs
    'Services/Mail/MicroPublicHelper.cs': (
        "<｜｜DSML｜｜ calls>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">source/App_Code/MicroPublicHelper.cs</｜｜DSML｜｜ parameter>\n"
        "<｜｜DSML｜｜ parameter name=\"offset\" string=\"false\">1855</｜｜DSML｜｜ parameter>\n"
        "<｜｜DSML｜｜ parameter name=\"returned_chars\" string=\"false\">4000</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    ),
    # 真实产出文件：Security/MicroAuthHelper.cs
    'Security/MicroAuthHelper.cs': (
        "<｜｜DSML｜｜ calls>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">source/App_Code/MicroPublicHelper.cs</｜｜DSML｜｜ parameter>\n"
        "<｜｜DSML｜｜ parameter name=\"offset\" string=\"false\">50000</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    ),
    # 真实产出文件：Handlers/CtrlMicroForm.ashx
    'Handlers/CtrlMicroForm.ashx': (
        "<｜｜DSML｜｜ calls>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">source/App_Code/MicroPublicHelper.cs</｜｜DSML｜｜ parameter>\n"
        "<｜｜DSML｜｜ parameter name=\"offset\" string=\"false\">1990</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    ),
    # 真实产出文件：Storage/Upload.ashx
    'Storage/Upload.ashx': (
        "<｜｜DSML｜｜ calls>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">output_code/MicroOA.Core/Infrastructure/Options/DatabaseOptions.cs</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "<｜｜DSML｜｜ invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">output_code/MicroOA.Core/Infrastructure/Hosting/HostingPipelineExtensions.cs</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "<｜｜DSML｜｜ invoke name=\"list_files\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">source/Resource/UploadFiles/Images/Avatar</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "<｜｜DSML｜｜ invoke name=\"list_files\">\n"
        "<｜｜DSML｜｜ parameter name=\"path\" string=\"true\">source/Resource/UploadFiles/Images/UploadImages</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    ),
    # 真实产出文件：[项目8666035c] tn-009784f3/MicroDBHelper.cs（无空格 tool_calls 变体）
    '[项目8666035c] tn-009784f3/MicroDBHelper.cs（无空格 tool_calls 变体）': (
        "<｜｜DSML｜｜tool_calls>\n"
        "<｜｜DSML｜｜invoke name=\"fs_read\">\n"
        "<｜｜DSML｜｜parameter name=\"offset\" string=\"false\">100000</｜｜DSML｜｜parameter>\n"
        "<｜｜DSML｜｜parameter name=\"path\" string=\"true\">source/App_Code/MicroDTHelper.cs</｜｜DSML｜｜parameter>\n"
        "<｜｜DSML｜｜parameter name=\"limit\" string=\"false\">500</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    ),
}


# ── 防误伤样本：正常代码 / 配置 / 文档，含 invoke / parameter / calls 等英文单词 ────────
BENIGN_SAMPLES = {
    # ① C# 真实方法调用：Invoke / InvokeAsync + 名为 parameter 的形参
    "output_code/MicroOA.Core/Middleware/FriendlyUrlMiddleware.cs": (
        "using System.Threading.Tasks;\n"
        "\n"
        "namespace MicroOA.Core.Middleware;\n"
        "\n"
        "public sealed class FriendlyUrlMiddleware\n"
        "{\n"
        "    private readonly RequestDelegate _next;\n"
        "\n"
        "    public async Task InvokeAsync(HttpContext context)\n"
        "    {\n"
        "        // 两次 calls：先本地重写，再 invoke 下一节点\n"
        "        var handler = ResolveHandler(context);\n"
        "        var parameter = context.Request.Query[\"id\"].ToString();\n"
        "        handler.Invoke(parameter);\n"
        "        await _next.Invoke(context);\n"
        "    }\n"
        "}\n"
    ),
    # ② C# XML 文档注释：字面含 <param name="parameter"> 与 invoke 描述
    "output_code/MicroOA.Core/Data/MicroDbHelper.cs": (
        "public static class MicroDbHelper\n"
        "{\n"
        "    /// <summary>Invoke a parameterized query. 该方法内部 calls SqlCommand。</summary>\n"
        "    /// <param name=\"parameter\">SQL 参数集合</param>\n"
        "    public static DataTable Query(string sql, SqlParameter[] parameter)\n"
        "    {\n"
        "        using var cmd = new SqlCommand(sql);\n"
        "        cmd.Parameters.AddRange(parameter);\n"
        "        return Execute(cmd);\n"
        "    }\n"
        "}\n"
    ),
    # ③ csproj / XML 配置：字面含 <parameter name=…> 节点（最像协议文本的正常形态）
    "output_code/MicroOA.Core/MicroOA.Core.csproj": (
        "<Project Sdk=\"Microsoft.NET.Sdk.Web\">\n"
        "  <PropertyGroup>\n"
        "    <TargetFramework>net8.0</TargetFramework>\n"
        "  </PropertyGroup>\n"
        "  <ItemGroup>\n"
        "    <PackageReference Include=\"ClosedXML\" Version=\"0.104.2\" />\n"
        "  </ItemGroup>\n"
        "  <!-- 旧 web.config 的规则原样搬迁：invoke handler by name -->\n"
        "  <handlers>\n"
        "    <invoke name=\"CtrlMicroForm\">\n"
        "      <parameter name=\"path\" string=\"true\">/CtrlMicroForm.ashx</parameter>\n"
        "    </invoke>\n"
        "  </handlers>\n"
        "</Project>\n"
    ),
    # ④ Razor 视图（前导下划线的约定文件名 + 视图里出现 invoke/calls 字样）
    "output_code/MicroOA.Core/Views/Shared/_Layout.cshtml": (
        "@using MicroOA.Core.Services\n"
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head><title>@ViewData[\"Title\"]</title></head>\n"
        "<body>\n"
        "    @* 此布局 calls RenderBody；不 invoke 任何服务端控件 *@\n"
        "    @RenderBody()\n"
        "    @await Html.PartialAsync(\"_Footer\")\n"
        "</body>\n"
        "</html>\n"
    ),
    # ⑤ Python 目标语言产出（含 parameter / calls / __call__ / __init__）
    "output_code/tools/__init__.py": (
        "\"\"\"包入口：暴露 invoke 与 calls 两个薄封装。\"\"\"\n"
        "\n"
        "\n"
        "def invoke(name: str, parameter: dict) -> dict:\n"
        "    \"\"\"按 name 分派并把 parameter 透传下去（内部 calls registry）。\"\"\"\n"
        "    handler = _REGISTRY[name]\n"
        "    return handler.__call__(parameter)\n"
    ),
    # ⑥ JS/前端资产（layui 类第三方库的典型写法）
    "output_code/wwwroot/js/micro-form.js": (
        "layui.use(['form'], function () {\n"
        "  var form = layui.form;\n"
        "  // 3 calls below: invoke render, bind parameter, submit\n"
        "  form.render();\n"
        "  form.on('submit(save)', function (data) {\n"
        "    var parameter = data.field;\n"
        "    return window.microForm.invoke('save', parameter);\n"
        "  });\n"
        "});\n"
    ),
    # ⑦ 中文文档：全角竖线 U+FF5C 作分隔符（真实取自 工作区/ 内的施工记录写法）
    "artifacts/p4/T-01-view-naming-limitation.md": (
        "# 视图命名限制\n"
        "\n"
        "**S-1｜加列（不跑迁移）**\n"
        "**S-2｜写迁移文件（仍不执行）**\n"
        "\n"
        "说明：Razor 的 _ViewStart / _ViewImports 属强制约定名。\n"
    ),
    # ⑧ 仅在注释里提到 DSML 这个词（判据是哨兵结构，不是关键词）
    "output_code/MicroOA.Core/Notes.cs": (
        "// 备注：真跑曾出现 DSML 协议原文被写进产出（D-P0-01），本文件只是提到它。\n"
        "public static class Notes { }\n"
    ),
}


# ══════════════════════════════════════════════════════════════════════════
# D-02：前导下划线必须原样保留
# ══════════════════════════════════════════════════════════════════════════

def test_leading_underscore_filenames_preserved():
    """6 个真跑受害用例（计划 §3 D-02 表格）必须原样落位。"""
    cases = [
        "output_code/App/Views/_ViewStart.cshtml",
        "output_code/App/Views/_ViewImports.cshtml",
        "output_code/App/Views/Shared/_Layout.cshtml",
        "output_code/pkg/__init__.py",
        "output_code/web/_partial.html",
        "output_code/scss/_variables.scss",
    ]
    for path in cases:
        assert _sanitize_write_path(path) == path, path


def test_leading_underscore_segment_level():
    # 主干前导 _ 保留；`__init__` 的尾部 __ 同样不得被剥（仅 rstrip 会削成 __init）
    assert _sanitize_segment("_ViewStart.cshtml") == "_ViewStart.cshtml"
    assert _sanitize_segment("__init__.py") == "__init__.py"
    assert _sanitize_segment("_variables.scss") == "_variables.scss"
    # 目录段的前导 _ 也保留（如 _shared / __tests__）
    assert _sanitize_write_path("output_code/_shared/__tests__/_helper.py") == \
        "output_code/_shared/__tests__/_helper.py"


# ══════════════════════════════════════════════════════════════════════════
# D-02 防回归：D-114「中文描述污染文件名」的既有能力不得削弱
# ══════════════════════════════════════════════════════════════════════════

def test_d114_ext_then_chinese_note_still_truncated():
    # 真跑实证污染（项目 8666035c）：扩展名后的中文括注仍须截断
    assert _sanitize_write_path(
        "output_code/tn-009784f3/MicroDBHelper.cs（QueryExcel 方法——OleDb 读取）"
    ) == "output_code/tn-009784f3/MicroDBHelper.cs"
    assert _sanitize_segment("Program.cs（入口）") == "Program.cs"


def test_d114_pure_cjk_stem_still_falls_back_not_bare_ext():
    """历史真跑案例：`集成测试.cs` 曾被削成名为 `cs` 的文件。不得再现。"""
    out = _sanitize_write_path("output_code/tests/集成测试.cs")
    tail = out.split("/")[-1]
    assert tail != "cs"
    assert tail.startswith("file_") and tail.endswith(".cs")
    assert all(ord(c) < 128 for c in tail)


def test_d114_all_chinese_filename_still_sanitized_legal():
    out = _sanitize_write_path("output_code/tn-64203bd5/JS、Layui 面板、服务器控件）")
    assert out.startswith("output_code/tn-64203bd5/")
    tail = out.split("/")[-1]
    assert tail
    assert all(ord(c) < 128 for c in tail)
    assert not any(ch in tail for ch in "（）、，。：")


def test_d114_legal_paths_and_fallbacks_unchanged():
    for p in ("output_code/MicroOA.Core/Services/AuthService.cs",
              "output_code/db/schema_poc.sql",
              "patches/tn-x/foo.diff",
              "output_code/MicroOA.Core/MicroOA.Core.csproj"):
        assert _sanitize_write_path(p) == p
    assert _sanitize_write_path("output_code/（）") == "output_code/file.txt"
    assert _sanitize_segment("  Services  ") == "Services"
    assert _sanitize_segment("..") == ""


# ══════════════════════════════════════════════════════════════════════════
# D-P0-01 正向：真实协议泄漏样本必须被拒写
# ══════════════════════════════════════════════════════════════════════════

def test_real_leak_samples_all_detected():
    assert len(DSML_LEAK_SAMPLES) == 6
    for name, content in DSML_LEAK_SAMPLES.items():
        assert detect_protocol_leak(content), name


async def test_leak_write_rejected_and_not_persisted(caplog):
    ws = _init()
    for rel, content in DSML_LEAK_SAMPLES.items():
        target_rel = "output_code/leak/" + rel.split("/")[-1].split("（")[0]
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=_TOOL_LOGGER):
            result = await tool_registry._execute_workspace_write(
                "fs_write_artifact",
                {"path": target_rel, "content": content},
                _PID, None,
            )
        assert result["status"] == "rejected", (rel, result)
        assert result["reason_code"] == PROTOCOL_LEAK_REASON_CODE
        assert "error" in result and result["error"]
        # 发声（公理 3）：不得静默拒写
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("D-P0-01" in r.getMessage() for r in warnings), rel
        # 拒写后文件确实未落盘
        assert not (ws / target_rel).exists(), target_rel


async def test_leak_write_does_not_overwrite_existing_good_file():
    """已有真实内容的文件不得被协议原文覆盖（等同 D-097：不静默污染目标）。"""
    ws = _init()
    rel = "output_code/MicroOA.Core/Security/MicroAuthHelper.cs"
    good = "namespace MicroOA.Core.Security;\npublic static class MicroAuthHelper { }\n"
    ok = await tool_registry._execute_workspace_write(
        "fs_write_artifact", {"path": rel, "content": good}, _PID, None)
    assert ok["status"] == "written"

    leak = DSML_LEAK_SAMPLES["Security/MicroAuthHelper.cs"]
    result = await tool_registry._execute_workspace_write(
        "fs_write_artifact", {"path": rel, "content": leak}, _PID, None)
    assert result["status"] == "rejected"
    assert (ws / rel).read_text("utf-8") == good


# ══════════════════════════════════════════════════════════════════════════
# D-P0-01 防误伤：正常代码一律放行
# ══════════════════════════════════════════════════════════════════════════

def test_benign_code_never_flagged():
    assert len(BENIGN_SAMPLES) >= 6
    for name, content in BENIGN_SAMPLES.items():
        assert detect_protocol_leak(content) is None, name


async def test_benign_code_actually_written():
    ws = _init()
    for rel, content in BENIGN_SAMPLES.items():
        result = await tool_registry._execute_workspace_write(
            "fs_write_artifact", {"path": rel, "content": content}, _PID, None)
        assert result["status"] == "written", (rel, result)
        assert (ws / result["path"]).read_text("utf-8") == content


def test_detector_empty_and_non_string_inputs():
    assert detect_protocol_leak("") is None
    assert detect_protocol_leak(None) is None
    assert detect_protocol_leak(123) is None  # type: ignore[arg-type]


def test_detector_returns_the_matched_marker():
    marker = detect_protocol_leak(DSML_LEAK_SAMPLES["Security/MicroAuthHelper.cs"])
    assert marker is not None
    assert "DSML" in marker
