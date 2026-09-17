"""V26.3 · R24 回归锁：只读工具能力洞与 write_scope 声明缺失。

  R24-04  `B-ACC-NO-READONLY-FILEGLOB`（P1）
          ① 新增 L1 只读递归文件名检索 `find_files`：走既有 `_confine` 路径约束、
             限结果条数、只返回路径不返内容、诚实回报 truncated；
          ② `run_safe_command` 显式声明 `write_scope="execute"`，分派判定收敛为
             单一归一化函数 `_effective_write_scope`（工具名特判不再散落在两个判定点）；
          ③ `run_safe_command` 的 L4 风险位与 Gate 触发行为**不得**因本改动发生任何变化
             （Q-ACC-2 复核已推翻"降级"这一修法方向）——本文件为此设了正面反锁。
"""

import pytest

from app.services import tool_registry, workspace_service


def _mk_ws(pid: str):
    workspace_service.init_workspace(pid)
    return workspace_service.workspace_path(pid)


def _touch(root, rel: str, content="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


async def _find(pid, **args):
    return await tool_registry._execute_read("find_files", args, pid)


# ══════════════════════════════════════════════════════════════════════════════
# ① find_files 能力
# ══════════════════════════════════════════════════════════════════════════════

class TestFindFiles:
    @pytest.mark.asyncio
    async def test_recursive_glob_finds_nested_matches(self, isolated_data):
        """核心能力：递归。这正是 list_files（`iterdir()` 不递归）缺的那一块。"""
        pid = "r24-find-1"
        root = _mk_ws(pid)
        _touch(root, "source/a/b/c/Web.csproj")
        _touch(root, "source/a/Other.sln")
        _touch(root, "source/a/b/readme.md")
        out = await _find(pid, pattern="*.csproj")
        assert out["match_count"] == 1
        assert out["files"][0]["path"] == "source/a/b/c/Web.csproj"
        assert out["truncated"] is False
        assert out["truncation_reason"] is None

    @pytest.mark.asyncio
    async def test_multiple_patterns_comma_separated(self, isolated_data):
        pid = "r24-find-2"
        root = _mk_ws(pid)
        _touch(root, "source/x/App.csproj")
        _touch(root, "source/Sln.sln")
        _touch(root, "source/x/Site.master")
        _touch(root, "source/x/ignore.txt")
        out = await _find(pid, pattern="*.csproj,*.sln,*.master")
        got = sorted(f["path"] for f in out["files"])
        assert got == ["source/Sln.sln", "source/x/App.csproj", "source/x/Site.master"]

    @pytest.mark.asyncio
    async def test_matches_filenames_not_contents(self, isolated_data):
        """与 code_grep 的分工：本工具匹配**文件名**，不看内容。"""
        pid = "r24-find-3"
        root = _mk_ws(pid)
        _touch(root, "source/notes.txt", content="这里面写了 App.csproj 三个字")
        out = await _find(pid, pattern="*.csproj")
        assert out["match_count"] == 0

    @pytest.mark.asyncio
    async def test_returns_paths_only_never_content(self, isolated_data):
        """只读且零内容泄漏面：返回结构里不得出现文件内容。"""
        pid = "r24-find-4"
        root = _mk_ws(pid)
        secret = "SUPER_SECRET_MARKER_9f3a"
        _touch(root, "source/creds.csproj", content=secret)
        out = await _find(pid, pattern="*.csproj")
        assert secret not in repr(out)
        assert set(out["files"][0].keys()) == {"path", "bytes"}

    @pytest.mark.asyncio
    async def test_path_escape_is_rejected_by_the_same_confine(self, isolated_data):
        """路径约束复用既有 `_confine()`，不另造 —— 越界即拒，不返回任何结果。"""
        pid = "r24-find-5"
        _mk_ws(pid)
        out = await _find(pid, pattern="*", path="../../../../etc")
        assert "error" in out and "越界" in out["error"]

    @pytest.mark.asyncio
    async def test_missing_pattern_is_an_honest_error(self, isolated_data):
        pid = "r24-find-6"
        _mk_ws(pid)
        out = await _find(pid, path="source")
        assert "error" in out and "pattern" in out["error"]

    @pytest.mark.asyncio
    async def test_missing_dir_is_an_honest_error(self, isolated_data):
        pid = "r24-find-7"
        _mk_ws(pid)
        out = await _find(pid, pattern="*.cs", path="source/nope")
        assert "error" in out and "目录不存在" in out["error"]

    @pytest.mark.asyncio
    async def test_result_cap_truncates_honestly(self, isolated_data):
        """结果上限：truncated=True 且 truncation_reason 指认原因（不静默截断）。"""
        pid = "r24-find-8"
        root = _mk_ws(pid)
        for i in range(7):
            _touch(root, f"source/f{i}.cs")
        out = await _find(pid, pattern="*.cs", max_results=3)
        assert out["match_count"] == 3
        assert out["truncated"] is True
        assert out["truncation_reason"] == "result_cap"

    @pytest.mark.asyncio
    async def test_max_results_cannot_exceed_hard_cap(self, isolated_data):
        pid = "r24-find-9"
        root = _mk_ws(pid)
        _touch(root, "source/a.cs")
        out = await _find(pid, pattern="*.cs", max_results=10 ** 9)
        # 请求值被夹到硬上限；此处只有 1 个文件，故断言"未截断"即可证明夹取未破坏正常结果
        assert out["match_count"] == 1 and out["truncated"] is False

    @pytest.mark.asyncio
    async def test_build_output_dirs_are_skipped(self, isolated_data):
        """bin/obj/node_modules/.git 不入结果：否则一次检索会被构建产物淹没。"""
        pid = "r24-find-10"
        root = _mk_ws(pid)
        _touch(root, "source/App.csproj")
        _touch(root, "source/bin/Debug/App.csproj")
        _touch(root, "source/node_modules/pkg/App.csproj")
        out = await _find(pid, pattern="*.csproj")
        assert [f["path"] for f in out["files"]] == ["source/App.csproj"]

    def test_tool_is_visible_to_the_agent_without_a_reseeded_db(self):
        """能力必须**真的到得了模型手里**：seed_all 只在表为空时插入，已 seed 过的库
        拿不到新增工具行 ⇒ 必须同时列进 _BUILTIN_SCHEMAS，否则模型永远看不见它，
        仍会去抓 L4。"""
        names = {s["function"]["name"] for s in tool_registry._BUILTIN_SCHEMAS}
        assert "find_files" in names
        entry = [s for s in tool_registry._BUILTIN_SCHEMAS
                 if s["function"]["name"] == "find_files"][0]
        assert entry["_risk_level"] == "L1"
        assert entry["_write_scope"] == "none"
        assert "pattern" in entry["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_builtin_dispatch_reaches_the_single_implementation(self, isolated_data):
        """无 DB 工具行时经 _execute_builtin 转发到同一份实现（不存在第二份逻辑）。"""
        pid = "r24-find-11"
        root = _mk_ws(pid)
        _touch(root, "source/deep/x/A.sln")
        out = await tool_registry._execute_builtin(
            "find_files", {"pattern": "*.sln"}, pid, "p0")
        assert [f["path"] for f in out["files"]] == ["source/deep/x/A.sln"]

    def test_seed_declares_the_tool_for_fresh_databases(self):
        from app.seed import RESOURCE_SEEDS as RESOURCES
        rows = [r for r in RESOURCES if r.get("name") == "find_files"]
        assert len(rows) == 1
        meta = rows[0]["type_metadata"]
        assert meta["write_scope"] == "none"
        assert rows[0]["risk_level"].value == "L1"


# ══════════════════════════════════════════════════════════════════════════════
# ② write_scope 声明 + 归一化
# ══════════════════════════════════════════════════════════════════════════════

class TestRunSafeCommandWriteScope:
    def test_seed_now_declares_execute_write_scope(self):
        from app.seed import RESOURCE_SEEDS as RESOURCES
        row = [r for r in RESOURCES if r.get("name") == "run_safe_command"][0]
        assert row["type_metadata"]["write_scope"] == "execute"

    def test_dry_run_supported_stays_false(self):
        """解除条件③ 的评估结论落成锁：`dry_run_supported` 在本仓**零 consumer**，
        改成 True 只会产出一个不存在的能力声称（D-049）⇒ 保持 False。"""
        from app.seed import RESOURCE_SEEDS as RESOURCES
        row = [r for r in RESOURCES if r.get("name") == "run_safe_command"][0]
        assert row["type_metadata"]["dry_run_supported"] is False

    def test_legacy_rows_without_declaration_still_route_to_provider(self):
        """安全反锁（本轮例外授权第④条）：**已 seed 过的库**里那行没有 write_scope。
        归一化必须把它补成 execute —— 否则它会落进只读分派，一个 L4 通用命令执行器被
        当成只读工具，那是真实的安全边界变更。"""
        assert tool_registry._effective_write_scope("run_safe_command", {}) == "execute"
        assert tool_registry._effective_write_scope("run_safe_command", None) == "execute"

    def test_declared_value_wins_over_fallback(self):
        assert tool_registry._effective_write_scope(
            "run_safe_command", {"write_scope": "system"}) == "system"

    def test_other_tools_default_to_none(self):
        assert tool_registry._effective_write_scope("fs_read", {}) == "none"
        assert tool_registry._effective_write_scope("find_files", {}) == "none"

    def test_risk_level_and_gate_threshold_unchanged(self):
        """`run_safe_command` 的 L4 与 Gate 阈值 L3 一律不动（Q-ACC-2）。"""
        from app.seed import RESOURCE_SEEDS as RESOURCES
        row = [r for r in RESOURCES if r.get("name") == "run_safe_command"][0]
        assert row["risk_level"].value == "L4"
        assert row["permission_scope"] == "exec_with_gate"
        assert row["type_metadata"]["requires_gate"] is True
        assert tool_registry._GATE_RISK_THRESHOLD == "L3"
        assert tool_registry._rank("L4") >= tool_registry._rank(
            tool_registry._GATE_RISK_THRESHOLD)
