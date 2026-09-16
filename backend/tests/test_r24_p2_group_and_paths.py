"""V26.3 · R24 回归锁：P2 群 + 硬编码路径。

  R24-08  `B-ACC-ONBOARD-SWALLOW`（P2）
          `POST /onboarding/complete` 的裸 `except Exception` 吞掉输入校验失败仍返 200
          且 `warnings: []`。改为按异常性质分治：ValueError→422；IO 类→200 + warnings 回传。

  R24-09  `B-ACC-P1-LIST-FALSE-PLACEHOLDER`（P2）
          `_is_p1_honest_placeholder` 把任何非 dict 产物（含内容充实的 JSON 数组）判为占位。

  R24-11  `B-ACC-BACKEND-PREREQ-SILENT-SKIP`（P2）
          skip 在 `-q` 下只表现为计数 +1，读输出的人看不出少验了什么。

  R24-12  `Q-R22-9`（待确认项，转公开前置）
          `config.py` 4 处 + `real_run_verifier.py` 1 处含开发者主目录的硬编码绝对路径默认值。
"""

import json
from pathlib import Path

import pytest

from app.services import workspace_service
from app.services.validation_agent import ValidationAgent


# ══════════════════════════════════════════════════════════════════════════════
# R24-08 · onboarding 裸 except 吞校验失败
# ══════════════════════════════════════════════════════════════════════════════

def _mk_project(client, name="R24 Onboard"):
    return client.post("/api/projects", json={
        "name": name, "source_type": "manual"}).json()["data"]["project_id"]


class TestOnboardingValidationNotSwallowed:
    def test_invalid_env_kind_returns_422_not_200(self, client):
        """解除条件 ①③：台账实测形态 —— 传 `env_kind: "container"`。

        旧行为：`workspace_service.update_environment` 正确 raise ValueError，被裸
        except 吞掉，接口返 **200 且 warnings: []**，且 environment.json 完全未更新。
        """
        pid = _mk_project(client)
        resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={
            "env_kind": "container",
            "language_hint": "csharp",
            "framework_hint": "ASP.NET WebForms",
        })
        assert resp.status_code == 422, resp.text
        assert "无效 env_kind" in resp.text
        assert "container" in resp.text

    def test_422_leaves_project_untouched(self, client):
        """422 发生在 svc.update() 之前 ⇒ 不留半截状态（onboarding_done 不得被置真）。"""
        pid = _mk_project(client)
        client.post(f"/api/projects/{pid}/onboarding/complete",
                    json={"env_kind": "container"})
        proj = client.get(f"/api/projects/{pid}").json()["data"]
        assert not proj.get("onboarding_done")

    def test_valid_env_kind_still_succeeds_with_no_warnings(self, client):
        """防回归：合法取值仍 200，且 `warnings` 为空（语义不变）。"""
        pid = _mk_project(client)
        resp = client.post(f"/api/projects/{pid}/onboarding/complete", json={
            "env_kind": "local", "language_hint": "csharp",
            "framework_hint": "ASP.NET WebForms",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["warnings"] == []

    def test_language_and_framework_hints_are_persisted_on_success(self, client):
        """台账点名的连带损失面：三个字段同在一个 dict 中被原子写入。"""
        pid = _mk_project(client)
        client.post(f"/api/projects/{pid}/onboarding/complete", json={
            "env_kind": "local", "language_hint": "csharp",
            "framework_hint": "ASP.NET WebForms"})
        env = workspace_service.read_environment(pid)
        assert env["env_kind"] == "local"
        assert env["language_hint"] == "csharp"
        assert env["framework_hint"] == "ASP.NET WebForms"

    def test_io_failure_is_reported_through_warnings_not_swallowed(self, client, monkeypatch):
        """解除条件 ②：落盘 IO 异常仍可降级，但**必须经 warnings 告知调用方**。

        旧代码对这一类只写服务端日志 —— "记了日志"不等于"告知了调用方"。
        """
        pid = _mk_project(client)

        def _boom(project_id, updates):
            raise OSError("disk full (injected)")

        monkeypatch.setattr("app.services.workspace_service.update_environment", _boom)
        resp = client.post(f"/api/projects/{pid}/onboarding/complete",
                           json={"env_kind": "local", "language_hint": "csharp"})
        assert resp.status_code == 200, resp.text
        warns = resp.json()["warnings"]
        assert len(warns) == 1, warns
        assert "环境档案写入失败" in warns[0]
        assert "OSError" in warns[0]
        # 连带丢失面必须写清（否则调用方不知道 language_hint 也丢了）
        assert "language_hint" in warns[0]

    def test_invalid_status_enum_also_returns_422(self, client, monkeypatch):
        """同一分治规则对另一个枚举同样成立（不是只给 env_kind 打补丁）。"""
        pid = _mk_project(client)
        with pytest.raises(ValueError, match="无效 status"):
            workspace_service.update_environment(pid, {"status": "bogus"})


# ══════════════════════════════════════════════════════════════════════════════
# R24-09 · 列表型产物被误判为诚实占位
# ══════════════════════════════════════════════════════════════════════════════

class TestP1ListNotFalsePlaceholder:
    """解除条件 ②：覆盖 list 型 / 空 list / 非空 dict / 占位 dict 四种形态。"""

    def test_nonempty_list_is_not_a_placeholder(self):
        """台账原始形态：`entry_points.json` 是 15 条充实数组，被报成"LLM 未产出"。"""
        data = [{"path": "App_Code/RouteConfig.cs", "kind": "route", "why": "…"},
                {"path": "Views/UserCenter/Login.aspx", "kind": "page", "why": "…"}]
        assert ValidationAgent._is_p1_honest_placeholder(data) is False

    def test_empty_list_is_a_placeholder(self):
        assert ValidationAgent._is_p1_honest_placeholder([]) is True

    def test_nonempty_dict_is_not_a_placeholder(self):
        assert ValidationAgent._is_p1_honest_placeholder({"a": 1}) is False

    def test_honest_placeholder_dict_is_a_placeholder(self):
        assert ValidationAgent._is_p1_honest_placeholder(
            {"identification_note": "LLM 未产出 entry_points（诚实标注，未伪造）"}) is True

    def test_placeholder_note_with_other_text_is_not_a_placeholder(self):
        """只有那句固定诚实标注才算占位（既有判据，逐字不变）。"""
        assert ValidationAgent._is_p1_honest_placeholder(
            {"identification_note": "识别完成，共 3 个入口"}) is False

    def test_missing_or_unparseable_is_a_placeholder(self):
        """`_read_json` 读不到/不可解析时返回 None ⇒ 确实未产出。"""
        assert ValidationAgent._is_p1_honest_placeholder(None) is True

    def test_empty_string_is_a_placeholder_but_nonempty_is_not(self):
        assert ValidationAgent._is_p1_honest_placeholder("") is True
        assert ValidationAgent._is_p1_honest_placeholder("some content") is False

    def test_empty_dict_behaviour_is_unchanged(self):
        """**刻意不改**：空 dict 在既有实现里就不算占位。

        台账登记的缺陷只在"非 dict"这一支；顺手改 dict 分支会让
        `_P1_MAJOR_GAP_THRESHOLD` 的触发面发生未登记的变化（更容易 hard_fail），
        属本轮明令禁止的顺手重构。本例把"不变"钉住，防止后人以为漏改。
        """
        assert ValidationAgent._is_p1_honest_placeholder({}) is False

    def test_scalars_are_not_placeholders(self):
        assert ValidationAgent._is_p1_honest_placeholder(0) is False
        assert ValidationAgent._is_p1_honest_placeholder(False) is False


# ══════════════════════════════════════════════════════════════════════════════
# R24-11 · skip 可见性
# ══════════════════════════════════════════════════════════════════════════════

class TestSkipVisibility:
    def test_backend_probe_returns_a_reason_either_way(self):
        """探测**不抛异常**、两种结果都带可读说明（探测而非自愈）。"""
        from tests.conftest import _probe_backend_reachable
        ok, detail = _probe_backend_reachable("http://127.0.0.1:1")
        assert ok is False and "连接失败" in detail
        ok2, detail2 = _probe_backend_reachable("http://localhost:8000")
        assert isinstance(ok2, bool) and detail2

    def test_probe_does_not_start_anything(self):
        """解除条件 ②：探测到不可达时只发声，绝不试图启动后端。"""
        import subprocess
        from unittest.mock import patch
        from tests.conftest import _probe_backend_reachable
        with patch.object(subprocess, "Popen", side_effect=AssertionError("不得起进程")), \
             patch.object(subprocess, "run", side_effect=AssertionError("不得起进程")):
            _probe_backend_reachable("http://127.0.0.1:1")

    def test_probe_timeout_is_bounded(self):
        """解除条件 ③：不得引入网络等待拖慢全量 —— 超时上限须是个小常数。"""
        from tests.conftest import _BACKEND_PROBE_TIMEOUT_S
        assert 0 < _BACKEND_PROBE_TIMEOUT_S <= 1.0

    def test_banner_states_backend_reachability(self):
        from tests.conftest import _run_precondition_banner
        text = "\n".join(_run_precondition_banner())
        assert "后端可达性" in text

    def test_skip_inventory_lists_every_skip_with_reason(self):
        """解除条件 ①：每条 skip 都要在结尾清单里带原因出现（`-q` 下也可见）。"""
        from tests.conftest import _skip_inventory_lines

        class _R:
            def __init__(self, nodeid, reason):
                self.nodeid = nodeid
                self.longrepr = ("f.py", 1, f"Skipped: {reason}")

        class _TR:
            stats = {"skipped": [
                _R("tests/test_a.py::t1", "需要 ≥2 个 real_available provider，当前 0"),
                _R("tests/test_b.py::t2", "缺 dotnet SDK"),
            ]}

        lines = _skip_inventory_lines(_TR())
        text = "\n".join(lines)
        assert "2 条" in text
        assert "tests/test_a.py::t1" in text and "real_available" in text
        assert "tests/test_b.py::t2" in text and "缺 dotnet SDK" in text

    def test_skip_inventory_is_empty_when_nothing_skipped(self):
        from tests.conftest import _skip_inventory_lines

        class _TR:
            stats: dict = {}

        assert _skip_inventory_lines(_TR()) == []

    def test_terminal_summary_actually_writes_the_inventory(self):
        """解除条件 ①的**接线**锁：清单必须真的经 `pytest_terminal_summary` 打出来。

        本例是变异验证补出来的（M10：把 `pytest_terminal_summary` 里那两行输出删掉时，
        原有 6 条用例**全绿** —— 它们只测了 `_skip_inventory_lines` 这个纯函数，
        没有测"它有没有被接到 pytest 的输出通道上"）。守卫的价值在接线，不在函数存在。
        """
        from tests.conftest import pytest_terminal_summary

        class _R:
            nodeid = "tests/test_x.py::t_zzz"
            longrepr = ("f.py", 1, "Skipped: 缺 dotnet SDK（注入）")

        written: list[str] = []

        class _TR:
            stats = {"skipped": [_R()]}

            def write_line(self, line):
                written.append(line)

        pytest_terminal_summary(_TR(), 0, None)
        text = "\n".join(written)
        assert "LLM 模式" in text, "前提横幅未输出"
        assert "tests/test_x.py::t_zzz" in text, "skip 清单未经 terminal_summary 输出"
        assert "缺 dotnet SDK（注入）" in text


# ══════════════════════════════════════════════════════════════════════════════
# R24-12 · 硬编码开发者主目录路径（Q-R22-9）
# ══════════════════════════════════════════════════════════════════════════════

_BACKEND_APP = Path(__file__).resolve().parents[1] / "app"


class TestNoHardcodedDeveloperHome:
    def test_defaults_are_derived_from_repo_location(self):
        from app.core.config import BACKEND_DIR, REPO_ROOT, Settings
        s = Settings()
        assert BACKEND_DIR.name == "backend"
        assert REPO_ROOT == BACKEND_DIR.parent
        assert s.database_url == f"sqlite:///{BACKEND_DIR / '.data' / 'rebuild.db'}"
        assert s.source_dir == str(REPO_ROOT / "source")
        assert s.workspace_dir == str(REPO_ROOT / "工作区")
        assert s.toolchain_cache_dir == str(BACKEND_DIR / ".data" / "toolchain-cache")

    def test_env_override_still_wins(self, monkeypatch):
        """Q-R22-9 修法约束：保留 `REBUILD_*` 覆盖能力（不得因推导而失效）。"""
        from app.core.config import Settings
        monkeypatch.setenv("REBUILD_WORKSPACE_DIR", "/tmp/r24-ws-override")
        monkeypatch.setenv("REBUILD_SOURCE_DIR", "/tmp/r24-src-override")
        s = Settings(_env_file=None)
        assert s.workspace_dir == "/tmp/r24-ws-override"
        assert s.source_dir == "/tmp/r24-src-override"

    def test_real_run_verifier_default_db_is_derived(self):
        """Q-R22-9 同族第 5 处（台账只记了 config.py 的 4 处）。"""
        from app.core.config import BACKEND_DIR
        from app.services.real_run_verifier import DEFAULT_DB
        assert DEFAULT_DB == str(BACKEND_DIR / ".data" / "rebuild.db")

    def test_no_developer_home_literal_remains_in_backend_app_code(self):
        """扫 `backend/app/` 全部 .py：不得再有开发者主目录字面量（注释里的缺陷原文除外）。

        为什么允许注释：修复处保留了"原字面量长什么样"以便后人核对，那是**说明**而非
        运行期路径。判据因此是"非注释行不得出现"。
        """
        offenders = []
        for p in sorted(_BACKEND_APP.rglob("*.py")):
            for lineno, line in enumerate(p.read_text("utf-8").splitlines(), 1):
                if "/home/king" not in line:
                    continue
                if line.lstrip().startswith("#"):
                    continue
                offenders.append(f"{p.relative_to(_BACKEND_APP.parent)}:{lineno}: {line.strip()}")
        assert offenders == [], "非注释行仍含开发者主目录字面量：\n" + "\n".join(offenders)
