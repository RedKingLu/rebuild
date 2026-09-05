"""R20-3 / R20-2-05 判据固化 —— 场景机制不得含硬编码场景值、分派分支或封闭枚举.

本文件把 ③§2 的 J-1 / J-1b / J-3 / J-4 / J-5 / J-6 判据固化为可复跑测试：断言全部打在
**真实源码文本与真实 skill 文件内容**上，不断言"函数可导入"。

两处判据措辞的实测修正（施工时发现，以实测为准）：

* **J-1b**：③§2 记载"施工前 `grep -rn "_generic" backend/app/` 零命中"，**实测为 13 命中** ——
  全部是既存方法名（`work_agent._plan_body_generic` / `_execute_generic` /
  `_build_generic_evidence_map` / `_build_generic_gate_brief`、
  `validation_agent._validate_generic` / `_verify_generic_evidence_map`），
  与场景兜底包目录名无关且早于场景机制存在。故本文件按**带引号的字面量** `"_generic"` 判定，
  它才真正对应"兜底包目录名只应出现在 scenario_loader"这一意图。

* **J-6**：③ 写"施工后 `^#.*信创` 期望 2"，主窗口裁定改为 1（`P-database-migrations` 的 H1
  已按 Q-R20-3-4 改掉）。但两条判据都**未排除新建的场景包目录** ——
  `source/skills/scenarios/xinchuang_switch/**` 按 R20-3-05/06 必须含信创内容，其 H1 就叫
  「信创切换」。故本文件对 skill 层判据一律**排除 `scenarios/` 子树**，并另设一条正向断言
  要求信创场景包确实含这些内容。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path("/home/king/rebuild")
BACKEND_APP = REPO_ROOT / "backend" / "app"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
SKILLS_ROOT = REPO_ROOT / "source" / "skills"
SCENARIOS_DIR = SKILLS_ROOT / "scenarios"

_CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
_XC_WORDS = ("信创", "达梦", "人大金仓", "openGauss", "麒麟", "统信")


def _code_files(*roots: Path) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix in _CODE_SUFFIXES and "__pycache__" not in p.parts:
                out.append(p)
    return out


def _matches(pattern: str, files: list[Path]) -> list[str]:
    rx = re.compile(pattern)
    hits: list[str] = []
    for p in files:
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(REPO_ROOT)}:{n}:{line.strip()}")
    return hits


def _skill_files(include_scenarios: bool) -> list[Path]:
    out = []
    for p in sorted(SKILLS_ROOT.rglob("*.md")):
        in_scenarios = SCENARIOS_DIR in p.parents
        if in_scenarios and not include_scenarios:
            continue
        out.append(p)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# J-1 / J-1b：场景值字面量不得出现在逻辑代码
# ─────────────────────────────────────────────────────────────────────────────

# 已登记 allowlist（每项须附理由）：
#   assessment_service.py:54 —— P2 assessment_report 的 JSON 输出子字段键（与
#   compatibility_hosting / database_migration / deployment_hosting 并列），不是场景 id，
#   早于场景机制存在。⚠️ 日后不得为"清理 J-1 命中"而改动该行 —— 会破坏 P2 报告 schema 键约定。
_J1_ALLOWLIST = {"backend/app/services/assessment_service.py:54"}


def test_j1_no_scenario_id_literals_in_logic_code():
    """典型场景 id 不得作为字面量出现在逻辑代码里（新增场景须零代码改动）。"""
    files = _code_files(BACKEND_APP / "services", BACKEND_APP / "graph",
                        BACKEND_APP / "schemas", BACKEND_APP / "api", FRONTEND_SRC)
    hits = _matches(r"\b(xinchuang_switch|modernization|porting)\b", files)
    unexpected = [h for h in hits if ":".join(h.split(":")[:2]) not in _J1_ALLOWLIST]
    assert not unexpected, (
        "逻辑代码出现场景 id 字面量（allowlist 之外）：\n" + "\n".join(unexpected))


def test_j1b_generic_pack_dir_name_only_in_scenario_loader():
    """兜底包目录名的字面量只应出现在 scenario_loader.py（它是机制常量的单一事实源）。"""
    files = [p for p in _code_files(BACKEND_APP) if p.name != "scenario_loader.py"]
    hits = _matches(r'"_generic"', files)
    assert not hits, "兜底包目录名字面量泄漏到 scenario_loader 之外：\n" + "\n".join(hits)


def test_j3_no_scenario_value_branches():
    """R20-2-05 核心判据：不得有以场景值为条件的分支（无分派面）。"""
    files = _code_files(BACKEND_APP, FRONTEND_SRC)
    hits = _matches(r"if[^\n]*scenario[^\n]*==|elif[^\n]*scenario|switch *\([^)]*scenario", files)
    assert not hits, "出现以场景值为条件的分派分支：\n" + "\n".join(hits)


def test_j4_no_scenario_enum_type():
    """场景不得被实现为固定枚举 / 封闭清单（AGENTS §10-27）。

    pattern 刻意按片段拼接：若把完整字面量写进本文件，本判据的原始 grep 会命中本文件自身
    （施工中实测踩到），给验收方制造假阳。
    """
    files = _code_files(REPO_ROOT / "backend", FRONTEND_SRC)
    pattern = (r"class +[A-Za-z]*Scen" + r"ario[A-Za-z]*\((str, *)?enum\.Enum"
               + r"|Scen" + r"arioEnum"
               + r"|SCENARIO" + r"_(LIST|CHOICES|OPTIONS)")
    hits = _matches(pattern, files)
    assert not hits, "出现场景枚举类型 / 封闭清单：\n" + "\n".join(hits)


def test_scenario_failure_codes_are_string_constants_not_enum():
    """失败 code 必须是模块级字符串常量：做成封闭 Enum 会与它服务的那条决策自相矛盾。"""
    from app.services import scenario_loader as sl
    assert isinstance(sl.SC_ID_INVALID, str) and sl.SC_ID_INVALID == "scenario-id-invalid"
    src = (BACKEND_APP / "services" / "scenario_loader.py").read_text(encoding="utf-8")
    imports = [l for l in src.splitlines()
               if re.match(r"^\s*(import enum\b|from enum import)", l)]
    assert not imports, "scenario_loader 不得引入 Enum（③§5.5 自洽性关键）：" + str(imports)


def test_scenario_loader_has_no_cache():
    """R20-3-02 是结构性判据：不得引入任何内容缓存（缓存直接制造该判据的失败路径）。"""
    src = (BACKEND_APP / "services" / "scenario_loader.py").read_text(encoding="utf-8")
    for banned in ("lru_cache", "@cache", "_CACHE", "cachetools", "TTLCache"):
        assert banned not in src, f"scenario_loader 出现缓存痕迹：{banned}"


# ─────────────────────────────────────────────────────────────────────────────
# J-5 / J-5b：装配与执行链路不得残留无条件信创口径
# ─────────────────────────────────────────────────────────────────────────────

_J5_FILES = [
    "context_layers.py",
    "context_assembler.py",
    "tech_selection_service.py",
    "validation_agent.py",
    "p4_execution_worker.py",
    "fusion_execution_engine.py",   # Q-R20-3-2 方案 B：本轮一并修
    "scenario_loader.py",
]


def test_j5_assembly_chain_has_no_unconditional_xinchuang_wording():
    """装配 / P1 选型 / P4 验收 / P4 执行 / fusion 链路的 prompt 常量须场景中立。"""
    for name in _J5_FILES:
        path = BACKEND_APP / "services" / name
        text = path.read_text(encoding="utf-8")
        assert "信创" not in text, f"{name} 仍含无条件信创口径"


def test_platform_identity_line_is_scenario_neutral():
    """身份句不得承载任何场景限定（场景信息经 scenario_line + C1 场景块供给）。"""
    from app.services.context_layers import build_system_prompt_from_layers, assemble_c1
    prompt = build_system_prompt_from_layers({"C1": assemble_c1()}, "p0", "node_worker_agent", "")
    assert "你是 rebuild 平台的 node_worker_agent。" in prompt
    for word in _XC_WORDS:
        assert word not in prompt, f"未选场景时 prompt 仍出现 {word}"


def test_frontend_overview_uses_open_two_tier_wording():
    """R20-3-08：前端不得再有"首期聚焦"式封闭表述，须用"典型场景…（包括但不限于）"。"""
    page = (FRONTEND_SRC / "pages" / "dashboard" / "OverviewPage.tsx").read_text(encoding="utf-8")
    assert "首期聚焦" not in page
    assert "包括但不限于" in page
    hits = _matches(r"首期聚焦|仅支持三种|只支持三种", _code_files(FRONTEND_SRC))
    assert not hits, "前端仍有封闭场景表述：\n" + "\n".join(hits)


# ─────────────────────────────────────────────────────────────────────────────
# J-6：skill 层封闭章节标题清零 + 内容一字未删
# ─────────────────────────────────────────────────────────────────────────────

OLD_SECTION = "## 信创迁移要点"
NEW_SECTION = "## 场景要点（按项目场景取用）"
# 免责语作用域升级（R20-3-03 补轮）：章节级前置语的作用域只及它所在那一节，
# 而含场景词的行有 80/123 散在 description / 适用阶段 / 输入 / 执行步骤 / 参考 等段落，
# 仍是无条件口径 ⇒ P0 判据「不得静默套用信创口径」未达成。故免责语改为
# **文档级**：紧跟 frontmatter 结束的 `---`、位于 H1 之前，覆盖全文所有举例。
OLD_PREFACE_MARK = "以下要点以**信创切换**场景为例"
DOC_DISCLAIMER_MARK = "**场景适用性说明**"
EXPECTED_RENAMED = 29
EXPECTED_FILES_WITH_XC_WORDS = 31


def test_j6a_closed_section_title_is_gone():
    files = _skill_files(include_scenarios=True)
    hits = [f"{p}" for p in files
            if any(l == OLD_SECTION for l in p.read_text(encoding="utf-8").splitlines())]
    assert not hits, "仍有封闭章节标题：\n" + "\n".join(hits)


def test_j6b_new_section_title_is_in_place():
    """改名章节仍在（服务 R20-3-08 消除封闭表述），且章节级前置语已被文档级免责语取代。"""
    files = _skill_files(include_scenarios=True)
    renamed = []
    for p in files:
        lines = p.read_text(encoding="utf-8").splitlines()
        renamed.extend(p for line in lines if line == NEW_SECTION)
        assert not any(OLD_PREFACE_MARK in l for l in lines), (
            f"{p} 仍有章节级前置语 —— 其作用域只及一节，应改为文档级免责语")
    assert len(renamed) == EXPECTED_RENAMED, f"改名章节数 {len(renamed)} != {EXPECTED_RENAMED}"


def test_j6b2_scenario_disclaimer_is_document_level():
    """R20-3-03（P0）：每个含场景词的 skill 必须有**文档级**免责语，位于 frontmatter 之后、H1 之前。

    章节级免责语覆盖不到 description / 适用阶段 / 输入 / 执行步骤 / 参考 等段落里的场景词，
    那些行仍是无条件口径。文档级免责语才能覆盖全文举例。
    """
    files = [p for p in _skill_files(include_scenarios=False)
             if any(w in p.read_text(encoding="utf-8") for w in _XC_WORDS)]
    assert len(files) == EXPECTED_FILES_WITH_XC_WORDS

    for p in files:
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "---", f"{p} 首行非 frontmatter 起始"
        close = next(i for i in range(1, len(lines)) if lines[i] == "---")
        assert DOC_DISCLAIMER_MARK in lines[close + 1], (
            f"{p}:{close + 2} frontmatter 之后未紧跟文档级免责语")
        disclaimer = lines[close + 1]
        for phrase in ("以信创切换场景为例", "不是唯一场景",
                       "source/skills/scenarios/<scenario>/", "migration_target",
                       "本文举例不得无条件套用"):
            assert phrase in disclaimer, f"{p} 免责语缺措辞「{phrase}」"
        h1 = next(i for i, l in enumerate(lines) if l.startswith("# "))
        assert h1 > close + 1, f"{p} 免责语未落在 H1 之前"

    # 每个含场景词的文件都有文档级免责语 ⇒ 无免责语覆盖的场景词行为 0
    uncovered = [
        f"{p.relative_to(REPO_ROOT)}:{n}"
        for p in _skill_files(include_scenarios=False)
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if any(w in line for w in _XC_WORDS)
        and DOC_DISCLAIMER_MARK not in line
        and p not in files
    ]
    assert not uncovered, "存在无免责语覆盖的场景词行：\n" + "\n".join(uncovered)


def test_j6c_no_closed_scenario_heading_outside_scenario_packs():
    """既有 34 个 skill 包里只允许剩 P-dotnet-patterns 的包标题（现代化与信创兼容并列）。"""
    hits = []
    for p in _skill_files(include_scenarios=False):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("#") and "信创" in line:
                hits.append(f"{p.relative_to(REPO_ROOT)}:{n}:{line}")
    assert len(hits) == 1, "期望仅剩 1 处（P-dotnet-patterns 包标题），实际：\n" + "\n".join(hits)
    assert "P-dotnet-patterns" in hits[0]


def test_j6d_no_xinchuang_content_was_deleted():
    """【最关键】改名不得删内容：既有 skill 包中含信创词的文件数必须不变（施工前 = 31）。"""
    files = [p for p in _skill_files(include_scenarios=False)
             if any(w in p.read_text(encoding="utf-8") for w in _XC_WORDS)]
    assert len(files) == EXPECTED_FILES_WITH_XC_WORDS, (
        f"含信创词的既有 skill 文件数 {len(files)} != {EXPECTED_FILES_WITH_XC_WORDS} —— "
        "若变小说明删了内容，违 Q-R20-PLAN-2 用户裁决")


def test_database_migrations_skill_title_is_no_longer_scenario_closed():
    """Q-R20-3-4：该 skill 按 category=p4 对所有 P4 项目加载，包标题不得写死为信创。"""
    p = SKILLS_ROOT / "p4" / "P-database-migrations" / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    assert "# P-database-migrations（数据库迁移执行）" in text
    assert "# P-database-migrations（信创数据库迁移执行）" not in text
    # description 保留信创国产库作为【含括的一类】，而非唯一范围
    assert "含信创国产库" in text
    # 正文的信创内容一字未删
    assert "达梦" in text and "openGauss" in text


def test_xinchuang_scenario_pack_keeps_the_domain_content():
    """R20-3-06 的另一面：信创知识不是被删掉，而是改由 xinchuang_switch 场景包供给。"""
    pack_dir = SCENARIOS_DIR / "xinchuang_switch"
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(pack_dir.glob("*")))
    for word in _XC_WORDS:
        assert word in joined, f"信创场景包缺少 {word}"


def test_scenario_packs_do_not_reuse_the_skill_layer_section_name():
    """防两层"场景要点"嵌套自指（③§1.2 / §5.2）。"""
    for p in _skill_files(include_scenarios=True):
        if SCENARIOS_DIR not in p.parents:
            continue
        assert NEW_SECTION not in p.read_text(encoding="utf-8"), \
            f"场景包 {p} 不得设名为「场景要点」的章节"
