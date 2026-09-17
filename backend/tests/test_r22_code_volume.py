"""R22-4 P5 源码/产出代码量对比 —— 非阻断【观察指标】测试。

本文件把 `产物/草稿/R22-③完善方案.md` §3 的 11 条判据逐条固化为可跑用例，
不停在文档承诺上。判据 ↔ 用例的对应关系写在每个测试类的 docstring 里。

最关键的一条是 `TestNonBlockingLock` —— 边界①（非阻断）的机器化：即便观察指标标了
`needs_manual_review=True`、即便它的度量或落盘抛异常，P5 既有验证结论必须**逐字不变**。
做法是用同一输入分别跑"接缝缺席（= 接入前）"与"接缝在位（含各种失败形态）"，
把全部 `SlotVerificationResult` 序列化成 JSON 后整体比对。
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import code_volume_service as cv
from app.services import full_stack_profiler as fsp
from app.services.p5_input_service import P4InputFacts
from app.services.p5_verification_service import P5VerificationService

CV_SOURCE = Path(cv.__file__).read_text(encoding="utf-8")
CV_TREE = ast.parse(CV_SOURCE)

# SKIP_DIRS 中"辨识度足够高、不可能作为普通英文词出现在代码里"的成员。
# （刻意排除 build / dist / vendor —— 它们是常见英文词，做裸文本查找会误报。）
_DISTINCTIVE_SKIP_NAMES = sorted(
    d for d in fsp.SKIP_DIRS if d.startswith(".") or "_" in d
)


# ── 公共构造 ───────────────────────────────────────────────────────────────

def _make_ws(tmp_path: Path, project_id: str = "p1") -> Path:
    ws = tmp_path / "projects" / project_id
    for d in ("source", "output_code", "patches", "artifacts", "evidence"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    return ws


def _write(ws: Path, rel: str, text: str) -> Path:
    target = ws / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _measure(ws: Path, project_id: str = "p1") -> dict:
    with patch("app.services.code_volume_service.workspace_path", return_value=ws):
        return cv.measure_code_volume(project_id)


def _measure_and_persist(ws: Path, project_id: str = "p1") -> dict | None:
    with patch("app.services.code_volume_service.workspace_path", return_value=ws):
        return cv.measure_and_persist(project_id)


def _read(ws: Path, project_id: str = "p1") -> dict | None:
    with patch("app.services.code_volume_service.workspace_path", return_value=ws):
        return cv.read_code_volume(project_id)


# ── 判据 1：SKIP_DIRS 复用（结构守卫）─────────────────────────────────────

class TestSkipDirsReuseGuard:
    """判据 1（结构守卫）：排除集必须复用 `full_stack_profiler.SKIP_DIRS` 单一事实源。

    为何这条是硬要求而非偏好：`B-R20-REDACT-THREE-IMPLS` 的教训 —— `hook_engine` 抄了
    一份 `_SECRET_PATTERNS` 副本（注释还自称 mirror），源侧新增第 4 条模式时副本没人跟随，
    导致一整类 URL 内嵌凭据在 pre-write 拦截上不设防。排除集同理：将来有人给源侧加一项
    （如 `target` / `.gradle`），副本不会跟随，度量结果会**悄悄失真**。
    沿用 `test_r21_execution_seam_consistency.py` 的守卫范式（AST + 反射，不引新依赖）。
    """

    def test_skip_dirs_is_the_very_same_object_not_a_copy(self):
        """身份断言：必须是同一个对象，不是"内容相等的另一份"。"""
        assert cv.SKIP_DIRS is fsp.SKIP_DIRS, (
            "code_volume_service.SKIP_DIRS 不再是 full_stack_profiler.SKIP_DIRS 本体 —— "
            "有人把它换成了副本/派生集合，源侧新增排除项将不会跟随（B-R20-REDACT-THREE-IMPLS）"
        )

    def test_module_defines_no_second_exclusion_collection(self):
        """AST 守卫：模块内任何集合字面量都不得包含 SKIP_DIRS 的成员。"""
        offenders: list[str] = []
        for node in ast.walk(CV_TREE):
            elems = None
            if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                elems = node.elts
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("set", "frozenset", "tuple", "list"):
                inner = node.args[0] if node.args else None
                elems = inner.elts if isinstance(inner, (ast.Set, ast.List, ast.Tuple)) else []
            if elems is None:
                continue
            for e in elems:
                if isinstance(e, ast.Constant) and isinstance(e.value, str) \
                        and e.value in fsp.SKIP_DIRS:
                    offenders.append(f"line {e.lineno}: {e.value!r}")
        assert not offenders, (
            "code_volume_service 里出现了自带的排除目录集合（禁止第二份副本，必须 import "
            f"full_stack_profiler.SKIP_DIRS）：{offenders}"
        )

    def test_module_source_never_spells_out_an_excluded_dir_name(self):
        """裸文本守卫：连注释/docstring 里都不该出现具体排除目录名。

        真出现了，说明有人开始在本模块里就地维护排除口径 —— 那是副本漂移的第一步。
        """
        leaked = [name for name in _DISTINCTIVE_SKIP_NAMES if name in CV_SOURCE]
        assert not leaked, (
            f"code_volume_service 源码中出现了排除目录名 {leaked} —— 排除口径的唯一事实源是 "
            "full_stack_profiler.SKIP_DIRS，本模块不得就地复述或维护"
        )


# ── 判据 2：排除生效（含嵌套）─────────────────────────────────────────────

class TestExclusionEffective:
    """判据 2：依赖/构建/平台自产目录（含**嵌套**层级）均不计入。"""

    def test_nested_and_toplevel_excluded_dirs_are_all_skipped(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "source/app.py", "a\nb\n")                       # 计入：2 行
        _write(ws, "source/deep/sub/keep.py", "k\n")                # 计入：1 行
        for excluded in sorted(fsp.SKIP_DIRS):
            # 顶层一份 + 嵌套两层一份，两种位置都必须被排除。
            _write(ws, f"source/{excluded}/lib.js", "x\ny\nz\n")
            _write(ws, f"source/pkg/inner/{excluded}/nested.js", "x\ny\nz\n")
        _write(ws, "output_code/out.cs", "o\n")

        rec = _measure(ws)

        assert rec["source"]["file_count"] == 2, (
            f"排除未生效，实际计入 {rec['source']['file_count']} 个文件："
            f"{rec['source']['by_extension']}")
        assert rec["source"]["line_count"] == 3
        assert set(rec["source"]["by_extension"]) == {".py"}, (
            "被排除目录里的 .js 文件泄漏进了扩展名分布（嵌套层级未按路径每一段检查）")

    def test_deeply_nested_node_modules_style_dir_is_excluded(self, tmp_path):
        """单点复现②记录的关键事实：排除须对**路径每一段**做，不是只看顶层。"""
        ws = _make_ws(tmp_path)
        deep = sorted(fsp.SKIP_DIRS)[0]
        _write(ws, f"source/a/b/c/{deep}/d/e/deep.py", "1\n2\n3\n4\n")
        _write(ws, "source/a/b/c/real.py", "1\n")

        rec = _measure(ws)

        assert rec["source"]["file_count"] == 1
        assert rec["source"]["line_count"] == 1


# ── 判据 3：行数口径 ───────────────────────────────────────────────────────

class TestLineCounting:
    """判据 3：已知内容的文件，行数精确（splitlines 文本行，不去注释/不去空行）。"""

    @pytest.mark.parametrize("content,expected", [
        ("", 0),
        ("one line no newline", 1),
        ("a\nb\nc\n", 3),
        ("a\nb\nc", 3),
        ("a\n\n\nb\n", 4),                      # 空行照计（不做"有效代码行"）
        ("# 全是注释\n# 还是注释\n", 2),          # 注释照计
        ("a\r\nb\r\n", 2),                      # CRLF 也是 2 行
    ])
    def test_line_count_is_exact(self, tmp_path, content, expected):
        ws = _make_ws(tmp_path)
        _write(ws, "source/f.py", content)
        rec = _measure(ws)
        assert rec["source"]["line_count"] == expected
        assert rec["source"]["by_extension"][".py"] == {"files": 1, "lines": expected}

    def test_by_extension_aggregates_files_and_lines(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n2\n")
        _write(ws, "source/b.cs", "1\n2\n3\n")
        _write(ws, "source/c.aspx", "1\n")
        rec = _measure(ws)
        assert rec["source"]["by_extension"][".cs"] == {"files": 2, "lines": 5}
        assert rec["source"]["by_extension"][".aspx"] == {"files": 1, "lines": 1}
        assert rec["source"]["file_count"] == 3
        assert rec["source"]["line_count"] == 6


# ── 判据 4：二进制 / 超大文件 ─────────────────────────────────────────────

class TestBinaryAndOversizeFiles:
    """判据 4：只计文件数不计行，且 measurement_gaps 如实累加（不静默跳过）。"""

    def test_binary_file_counted_but_lines_not_counted(self, tmp_path):
        ws = _make_ws(tmp_path)
        (ws / "source" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\x00\xfd")
        _write(ws, "source/real.py", "1\n2\n")

        rec = _measure(ws)

        assert rec["source"]["file_count"] == 2          # 文件数照计
        assert rec["source"]["line_count"] == 2          # 只有文本文件的行被计
        assert rec["source"]["by_extension"][".png"] == {"files": 1, "lines": 0}
        assert rec["measurement_gaps"]["binary_or_skipped_lines"] == 1
        assert rec["measurement_gaps"]["unreadable_files"] == 0

    def test_single_file_read_cap_is_two_megabytes(self):
        """上限现值锚点：改动它是有意识的口径变更，应当让测试失败提醒施工者。"""
        assert cv.MAX_READ_BYTES == 2 * 1024 * 1024

    def test_oversize_file_counted_but_lines_not_counted(self, tmp_path):
        ws = _make_ws(tmp_path)
        (ws / "source" / "huge.sql").write_bytes(b"x\n" * (1024 * 1024 + 8))
        _write(ws, "source/small.sql", "1\n")

        rec = _measure(ws)

        assert rec["source"]["file_count"] == 2
        assert rec["source"]["line_count"] == 1          # 巨型文件的行不计
        assert rec["measurement_gaps"]["binary_or_skipped_lines"] == 1

    def test_utf16_bom_file_is_still_counted_as_text(self, tmp_path):
        """.NET/WebForms 源码常见 UTF-16 BOM；照 full_stack_profiler._read_text 口径解码。"""
        ws = _make_ws(tmp_path)
        (ws / "source" / "Default.aspx.cs").write_bytes("a\nb\nc\n".encode("utf-16"))
        rec = _measure(ws)
        assert rec["source"]["line_count"] == 3
        assert rec["measurement_gaps"]["binary_or_skipped_lines"] == 0


# ── 判据 5：读取失败不静默 ────────────────────────────────────────────────

class TestUnreadableFilesAreNotSilent:
    """判据 5：读取抛异常 → 计入 unreadable_files **并且**发出 warning（公理 3）。"""

    def test_read_failure_counted_and_warned(self, tmp_path, monkeypatch, caplog):
        ws = _make_ws(tmp_path)
        _write(ws, "source/locked.cs", "1\n2\n3\n")
        _write(ws, "source/ok.cs", "1\n")

        original = Path.read_bytes

        def _boom(self):
            if self.name == "locked.cs":
                raise PermissionError("_FAKE_PERMISSION_DENIED for test")
            return original(self)

        monkeypatch.setattr(Path, "read_bytes", _boom)

        with caplog.at_level("WARNING", logger="app.services.code_volume_service"):
            rec = _measure(ws)

        assert rec["measurement_gaps"]["unreadable_files"] == 1
        assert rec["measurement_gaps"]["binary_or_skipped_lines"] == 0
        assert rec["source"]["file_count"] == 2          # 文件数仍如实计
        assert rec["source"]["line_count"] == 1          # 读不到的不当作 0 行掺进行数
        assert any("locked.cs" in r.getMessage() for r in caplog.records), (
            "读取失败被静默了 —— 必须发声（公理 3 / AGENTS §10-21）")


# ── 判据 6：分母为 0 → null ───────────────────────────────────────────────

class TestRatioDenominatorZero:
    """判据 6：分母为 0 时 ratio 为 null，**不是** 0，也不是 inf。"""

    def test_source_dir_missing_gives_null_ratio(self, tmp_path):
        ws = tmp_path / "projects" / "p1"
        _write(ws, "output_code/x.py", "1\n")
        rec = _measure(ws)
        assert rec["ratio"] == {"by_file_count": None, "by_line_count": None}
        assert rec["measurement_gaps"]["source_missing"] is True

    def test_source_present_but_empty_gives_null_ratio(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "output_code/x.py", "1\n")
        rec = _measure(ws)
        for key, value in rec["ratio"].items():
            assert value is None, f"ratio.{key} 应为 null，实际 {value!r}"
            assert value != 0
        assert rec["measurement_gaps"]["source_missing"] is False

    def test_source_files_but_zero_lines_gives_null_line_ratio_only(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "source/empty.cs", "")               # 有文件、0 行
        _write(ws, "output_code/x.py", "1\n2\n")
        rec = _measure(ws)
        assert rec["ratio"]["by_line_count"] is None    # 行数分母为 0
        assert rec["ratio"]["by_file_count"] == 1.0     # 文件数分母不为 0，正常给比值

    def test_ratio_is_never_infinity_or_nan(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "output_code/x.py", "1\n")
        rec = _measure(ws)
        for value in rec["ratio"].values():
            assert value is None or (value == value and abs(value) != float("inf"))
        # 落盘后仍是合法 JSON（Infinity/NaN 会让 json 产出非标准字面量）。
        assert "Infinity" not in json.dumps(rec) and "NaN" not in json.dumps(rec)


# ── 判据 7a / 7b：结构性异常两种 ──────────────────────────────────────────

class TestStructuralAnomalies:
    """判据 7a：产出目录不存在 / 有效文件数为 0；判据 7b：有文件但总行数为 0。

    两条都与阈值无关（边界③：本轮不落任何比例判断），只标"需人工复核"，
    **不给**"产出物无实质内容"的结论（边界④）。
    """

    def test_7a_output_dir_missing_flags_manual_review(self, tmp_path):
        ws = tmp_path / "projects" / "p1"
        _write(ws, "source/a.cs", "1\n")
        rec = _measure(ws)
        assert rec["needs_manual_review"] is True
        assert rec["review_reasons"] and rec["review_reasons"][0].startswith("output_missing")
        assert rec["measurement_gaps"]["output_missing"] is True
        assert rec["status"] == "measured"              # 度量本身是成功的

    def test_7a_output_dir_present_but_all_files_excluded(self, tmp_path):
        """产出侧只剩被排除目录里的文件 ⇒ 有效文件数为 0，同属结构性异常。"""
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n")
        _write(ws, f"output_code/{sorted(fsp.SKIP_DIRS)[0]}/generated.js", "1\n2\n")
        rec = _measure(ws)
        assert rec["output"]["file_count"] == 0
        assert rec["needs_manual_review"] is True
        assert rec["review_reasons"][0].startswith("output_empty")

    def test_7b_output_files_present_but_all_empty(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n2\n")
        _write(ws, "output_code/a.py", "")
        _write(ws, "output_code/b.py", "")
        rec = _measure(ws)
        assert rec["output"]["file_count"] == 2
        assert rec["output"]["line_count"] == 0
        assert rec["needs_manual_review"] is True
        assert rec["review_reasons"][0].startswith("output_all_lines_zero")

    def test_healthy_pair_needs_no_manual_review(self, tmp_path):
        """反向锁：正常产出不得被标"需人工复核"（否则这个信号就没意义了）。"""
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n2\n3\n")
        _write(ws, "output_code/a.py", "1\n2\n")
        rec = _measure(ws)
        assert rec["needs_manual_review"] is False
        assert rec["review_reasons"] == []

    def test_low_ratio_alone_never_flags_review(self, tmp_path):
        """边界③的机器化：比例极低也不标复核 —— 本轮**没有**任何比例判断。"""
        ws = _make_ws(tmp_path)
        for i in range(50):
            _write(ws, f"source/f{i}.cs", "x\n" * 100)
        _write(ws, "output_code/only.py", "1\n")
        rec = _measure(ws)
        assert rec["needs_manual_review"] is False, (
            "出现了比例类判断 —— 边界③：无真实规模样本前不得设任何阈值")
        assert rec["ratio"]["by_line_count"] is not None    # 比值照给，只是不判


# ── 判据 8：度量失败 → unknown ────────────────────────────────────────────

class TestMeasurementFailureIsUnknown:
    """判据 8：度量本身失败 → status="unknown" 且 needs_manual_review=True，绝不默认 measured。

    承 `heartbeat_service` 的诚实三态范式（alive/stalled/unknown，从不默认判活）。
    """

    def test_scan_failure_reports_unknown_not_measured(self, tmp_path, monkeypatch, caplog):
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n")
        _write(ws, "output_code/a.py", "1\n")

        def _boom(root):
            raise OSError("_FAKE_SCAN_FAILURE for test")

        monkeypatch.setattr(cv, "_measure_side", _boom)

        with caplog.at_level("WARNING", logger="app.services.code_volume_service"):
            rec = _measure(ws)

        assert rec["status"] == "unknown", "度量失败绝不能默认报 measured"
        assert rec["needs_manual_review"] is True
        assert rec["review_reasons"] and "measurement_failed" in rec["review_reasons"][0]
        assert rec["ratio"] == {"by_file_count": None, "by_line_count": None}
        assert rec["source"] == {"file_count": 0, "line_count": 0, "by_extension": {}}
        # 没扫成功就不能声称"不缺失" —— 用 None 而不是 False。
        assert rec["measurement_gaps"]["source_missing"] is None
        assert rec["measurement_gaps"]["output_missing"] is None
        assert caplog.records, "度量失败被静默了（公理 3）"

    def test_status_is_never_hardcoded_measured_on_the_failure_path(self, tmp_path, monkeypatch):
        """连 measure_and_persist 这层也不得把 unknown 洗成 measured。"""
        ws = _make_ws(tmp_path)
        monkeypatch.setattr(cv, "_measure_side",
                            lambda root: (_ for _ in ()).throw(OSError("_FAKE_")))
        rec = _measure_and_persist(ws)
        assert rec is not None and rec["status"] == "unknown"
        assert _read(ws)["status"] == "unknown"     # 落盘的也是 unknown


# ── 判据 9【最关键】：非阻断锁定断言 ──────────────────────────────────────

def _snapshot(results: list) -> str:
    """把全部 SlotVerificationResult 逐字序列化，供"接入前后"整体比对。"""
    return json.dumps(
        [{"slot_id": r.slot_id, "passed": r.passed, "status": r.status,
          "evidence_refs": r.evidence_refs, "artifacts": r.artifacts,
          "issues": r.issues, "details": r.details} for r in results],
        sort_keys=True, ensure_ascii=False, default=str)


def _build_p5_case(tmp_path: Path, output_rel: str):
    """构造 5 硬必需槽位全通过的 P5 输入（照 test_r12_c4 既有范式）。

    `output_rel` 让调用方选择产出文件的落点：放在被排除目录下时，P5 槽位照样通过
    （文件真实存在且非空），而代码量观察指标会因"有效文件数为 0"标 needs_manual_review
    —— 正好用来验证"标了复核也绝不改变 P5 结论"。
    """
    ws = _make_ws(tmp_path)
    code = _write(ws, output_rel, "real code\n")
    sha = hashlib.sha256(code.read_bytes()).hexdigest()
    _write(ws, "patches/tn-001.diff", "diff content")
    _write(ws, "source/legacy.cs", "1\n2\n3\n")
    ev = {"evidence_id": "ev-001", "stage": "p4", "output_code_ref": output_rel,
          "output_sha256": sha, "status": "validated"}
    _write(ws, "evidence/ev-001.json", json.dumps(ev))
    svc = P5VerificationService(tracer=MagicMock(), auditor=MagicMock(), aet=MagicMock())
    svc.aet.list_evidence.return_value = [ev]
    p4 = P4InputFacts(project_id="p1", run_id="r", blocked=False,
                      p4_to_p5_gate_status="approved",
                      output_code_refs=[output_rel],
                      patch_refs=["patches/tn-001.diff"],
                      evidence_refs=["ev-001"],
                      p4_execution_summary={"stage": "p4", "graph_status": "completed",
                                            "change_manifest": []})
    return svc, p4, ws


class _ExplodingMediator:
    """落盘时抛异常的写闸替身（模拟 artifacts/ 不可写）。"""

    def __init__(self, *_a, **_kw):
        pass

    def check_write(self, *_a, **_kw):
        raise OSError("_FAKE_WRITE_GATE_FAILURE for test")


def _run_p5(svc, p4, ws, seam: str) -> str:
    """跑一次 P5 硬必需验证，返回结论快照。`seam` 选择观察指标接缝的形态。"""
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("app.services.p5_verification_service.workspace_path", return_value=ws))
        stack.enter_context(
            patch("app.services.code_volume_service.workspace_path", return_value=ws))
        if seam == "absent":
            # 「接入前」：接缝什么也不做，等价于本次改动尚未落地。
            stack.enter_context(patch.object(cv, "measure_and_persist", lambda pid: None))
        elif seam == "measure_raises":
            stack.enter_context(patch.object(
                cv, "measure_and_persist",
                MagicMock(side_effect=RuntimeError("_FAKE_MEASURE_FAILURE"))))
        elif seam == "persist_raises_inside":
            stack.enter_context(patch.object(cv, "WorkspaceMediator", _ExplodingMediator))
        elif seam == "persist_raises_outward":
            stack.enter_context(patch.object(
                cv, "persist_code_volume",
                MagicMock(side_effect=OSError("_FAKE_PERSIST_FAILURE"))))
        elif seam != "real":
            raise AssertionError(f"未知接缝形态 {seam!r}")
        return _snapshot(svc.verify_all_hard_required("p1", p4))


class TestNonBlockingLock:
    """判据 9【最关键】：边界①（非阻断）的机器化 —— P5 结论逐字不变。

    这条不得省略。它同时覆盖：
      - 观察指标标了 needs_manual_review=True；
      - 度量抛异常（接缝自身的 try/except 兜住）；
      - 落盘抛异常（写闸失败 / persist 向外抛）。
    以上任一情形下，`verify_all_hard_required` 返回的全部 SlotVerificationResult
    必须与"接缝缺席"时**逐字相同**。
    """

    @pytest.mark.parametrize("output_rel,expect_review", [
        ("output_code/migrated.py", False),
        (f"output_code/{sorted(fsp.SKIP_DIRS)[0]}/migrated.py", True),
    ])
    def test_p5_conclusion_identical_with_and_without_seam(self, tmp_path, output_rel,
                                                           expect_review):
        svc, p4, ws = _build_p5_case(tmp_path, output_rel)

        baseline = _run_p5(svc, p4, ws, seam="absent")
        assert '"passed": true' in baseline      # 基线确有真实结论，比对不是空转

        real = _run_p5(svc, p4, ws, seam="real")
        assert real == baseline, "接入观察指标后 P5 结论发生了变化 —— 违反边界①（非阻断）"

        # 证明比对非空转：接缝真的跑了，且真的标了（或没标）复核。
        record = _read(ws)
        assert record is not None, "接缝没有真正执行 —— 上面的比对是空转"
        assert record["needs_manual_review"] is expect_review

        for seam in ("measure_raises", "persist_raises_inside", "persist_raises_outward"):
            assert _run_p5(svc, p4, ws, seam=seam) == baseline, (
                f"接缝以 {seam} 形态失败时 P5 结论发生了变化 —— 违反边界①")

        # 观察产物已存在于工作区之后再跑一次，结论仍与基线一致。
        assert _run_p5(svc, p4, ws, seam="absent") == baseline

    def test_no_slot_result_ever_carries_code_volume_data(self, tmp_path):
        """边界①的第二半：结果对象里不得夹带任何代码量字段（严禁并入槽位结果）。"""
        svc, p4, ws = _build_p5_case(tmp_path, "output_code/migrated.py")
        snapshot = _run_p5(svc, p4, ws, seam="real")
        for forbidden in ("code_volume", "line_count", "needs_manual_review",
                          "review_reasons", "measurement_gaps", "by_extension"):
            assert forbidden not in snapshot, (
                f"SlotVerificationResult 里出现了代码量字段 {forbidden!r} —— "
                "观察指标必须是纯旁路，不得并入验证结论")

    def test_seam_is_actually_wired_into_verify_all_hard_required(self, tmp_path):
        """接缝在位性：verify_all_hard_required 每轮调用度量恰好一次。"""
        svc, p4, ws = _build_p5_case(tmp_path, "output_code/migrated.py")
        spy = MagicMock(return_value=None)
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws), \
             patch.object(cv, "measure_and_persist", spy):
            svc.verify_all_hard_required("p1", p4)
        spy.assert_called_once_with("p1")

    def test_verify_all_hard_required_still_returns_exactly_five_slots(self, tmp_path):
        """回归锁：接缝不得多塞/少塞槽位。"""
        svc, p4, ws = _build_p5_case(tmp_path, "output_code/migrated.py")
        with patch("app.services.p5_verification_service.workspace_path", return_value=ws), \
             patch("app.services.code_volume_service.workspace_path", return_value=ws):
            results = svc.verify_all_hard_required("p1", p4)
        assert len(results) == 5
        assert all(r.passed for r in results), [(r.slot_id, r.status) for r in results]


# ── 判据 10：无比例常量 ───────────────────────────────────────────────────

class TestNoRatioThresholdConstants:
    """判据 10：源码中不存在任何硬编码比例阈值（防"顺手"加阈值把观察变隐形 Gate）。"""

    # 允许出现的数字字面量白名单（各有明确非阈值用途）：
    #   0    —— 分母/计数是否为零的判断
    #   1/2  —— 单文件读取上限的因子
    #   6    —— 比值保留的小数位数
    #   1024 —— 字节换算
    _ALLOWED_NUMBERS = {0, 1, 2, 6, 1024}

    def _numeric_constants(self):
        for node in ast.walk(CV_TREE):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                    and not isinstance(node.value, bool):
                yield node

    def test_no_float_literal_anywhere(self):
        floats = [f"line {n.lineno}: {n.value!r}" for n in self._numeric_constants()
                  if isinstance(n.value, float)]
        assert not floats, (
            f"出现了浮点字面量 {floats} —— 极可能是比例阈值（边界③：本轮不设任何阈值，"
            "'多低算异常'须待真实规模样本）")

    def test_every_numeric_literal_is_on_the_non_threshold_allowlist(self):
        unexpected = sorted({n.value for n in self._numeric_constants()}
                            - self._ALLOWED_NUMBERS)
        assert not unexpected, (
            f"出现了白名单外的数字字面量 {unexpected} —— 若是新的比例/百分比阈值则违反边界③；"
            "若确有正当非阈值用途，请连同理由一起加进 _ALLOWED_NUMBERS")

    def test_no_threshold_flavoured_module_constant(self):
        banned = ("RATIO", "THRESHOLD", "PERCENT", "PCT", "MIN_LINES", "MIN_FILES")
        offenders = [t.id for node in CV_TREE.body if isinstance(node, ast.Assign)
                     for t in node.targets
                     if isinstance(t, ast.Name) and any(b in t.id.upper() for b in banned)]
        assert not offenders, (
            f"出现了阈值形态的模块级常量 {offenders} —— 边界③禁止（ratio 字段只作观察值，"
            "不得有任何判定阈值）")


# ── 落盘与只读查询（边界②：单一写闸、不加 API 端点）────────────────────

class TestPersistAndRead:
    """落盘经 WorkspaceMediator 单一写闸（D-099⑥）；只读查询诚实返回 None。"""

    def test_persist_lands_at_the_expected_artifact_path(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n")
        _write(ws, "output_code/a.py", "1\n")
        rel = None
        with patch("app.services.code_volume_service.workspace_path", return_value=ws):
            rec = cv.measure_code_volume("p1")
            rel = cv.persist_code_volume("p1", rec)
        assert rel == "artifacts/p5/_code_volume.json"
        on_disk = json.loads((ws / rel).read_text(encoding="utf-8"))
        assert on_disk["schema"] == "code_volume_v1"
        assert on_disk == rec

    def test_write_target_is_accepted_by_the_real_write_gate(self, tmp_path):
        """落盘路径必须是写闸放行的目标（artifacts/ 可写、source/ 恒拒）。"""
        from app.services.workspace_mediator import WorkspaceMediator
        ws = _make_ws(tmp_path)
        mediator = WorkspaceMediator(str(ws))
        target, risk = mediator.check_write(cv.CODE_VOLUME_REL_PATH)
        assert target == (ws / cv.CODE_VOLUME_REL_PATH)
        assert risk == "L2"
        with pytest.raises(ValueError):
            mediator.check_write("source/_code_volume.json")   # 源只读，D-099①

    def test_persist_failure_is_advisory_not_raised(self, tmp_path, caplog):
        ws = _make_ws(tmp_path)
        with patch("app.services.code_volume_service.workspace_path", return_value=ws), \
             patch.object(cv, "WorkspaceMediator", _ExplodingMediator), \
             caplog.at_level("WARNING", logger="app.services.code_volume_service"):
            rel = cv.persist_code_volume("p1", {"schema": "code_volume_v1"})
        assert rel is None                      # 诚实返回 None
        assert caplog.records                   # 但发声了（公理 3）

    def test_read_returns_none_when_never_measured(self, tmp_path):
        ws = _make_ws(tmp_path)
        assert _read(ws) is None

    def test_read_returns_none_on_corrupt_artifact(self, tmp_path, caplog):
        ws = _make_ws(tmp_path)
        _write(ws, cv.CODE_VOLUME_REL_PATH, "{ not json")
        with caplog.at_level("WARNING", logger="app.services.code_volume_service"):
            assert _read(ws) is None
        assert caplog.records

    def test_measure_and_persist_roundtrip(self, tmp_path):
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n2\n")
        _write(ws, "output_code/a.py", "1\n")
        rec = _measure_and_persist(ws)
        assert rec is not None and rec["status"] == "measured"
        assert _read(ws) == rec

    def test_measurement_never_writes_into_source(self, tmp_path):
        """源码侧是 D-099① 只读区：度量只读，不得留下任何痕迹。"""
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n")
        _write(ws, "output_code/a.py", "1\n")
        before = sorted(p.relative_to(ws).as_posix() for p in (ws / "source").rglob("*"))
        _measure_and_persist(ws)
        after = sorted(p.relative_to(ws).as_posix() for p in (ws / "source").rglob("*"))
        assert before == after

    def test_record_has_exactly_the_agreed_field_structure(self, tmp_path):
        """字段结构锚点（R22-③完善方案 §1.3）。"""
        ws = _make_ws(tmp_path)
        _write(ws, "source/a.cs", "1\n")
        _write(ws, "output_code/a.py", "1\n")
        rec = _measure(ws)
        assert set(rec) == {"schema", "measured_at", "source", "output", "ratio",
                            "needs_manual_review", "review_reasons",
                            "measurement_gaps", "status"}
        assert set(rec["source"]) == {"file_count", "line_count", "by_extension"}
        assert set(rec["output"]) == set(rec["source"])
        assert set(rec["ratio"]) == {"by_file_count", "by_line_count"}
        assert set(rec["measurement_gaps"]) == {"unreadable_files", "binary_or_skipped_lines",
                                                "source_missing", "output_missing"}
        assert rec["status"] in ("measured", "unknown")
