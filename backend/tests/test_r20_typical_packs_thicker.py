"""R20-3-05 —— 典型场景包"资源相对更丰富"的可验证判据（读真实包目录）.

阈值取值 = 用户裁决 Q-R20-3-1 方案 A。三者均为**命名常量**（禁魔数）：

    THICKNESS_RATIO           = 2.0   典型包相对 _generic 的倍数下限
    MIN_TYPICAL_CANDIDATES    = 3     典型包选型候选数下限（对齐信创三候选）
    MIN_TYPICAL_RESOURCE_REFS = 3     典型包资源引用数下限

度量维度按抗灌水程度分层（③§6.4）：
  主指标（难灌水，须与现实世界真实技术事实对应）：候选数 / 风险条数 / 锚点条数
  辅指标（可自动校验存在性，防空引用刷数）：related_skills + related_resources 数
  硬门槛：四文件齐备（R20-3-04 直接要求）
  弱辅助（最易灌水，不作唯一依据）：SKILL.md 正文字符数

阈值以 `_generic` 为**动态基准**（比例而非绝对魔数）——`_generic` 变厚时典型包要求随之提高，
符合"典型 = 相对更丰富"的相对语义。

判据不得放宽：若某典型包不达标，应诚实判 P1 并补内容，**不得调低 THICKNESS_RATIO 凑过**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.scenario_loader import (
    GENERIC_SCENARIO_DIR,
    TIER_TYPICAL,
    list_scenarios,
    resolve_scenario_pack,
    scenarios_root,
)

# ── 用户裁决 Q-R20-3-1 方案 A（命名常量，禁魔数）──────────────────────────────
THICKNESS_RATIO = 2.0
MIN_TYPICAL_CANDIDATES = 3
MIN_TYPICAL_RESOURCE_REFS = 3

SKILLS_ROOT = Path("/home/king/rebuild/source/skills")
RESOURCES_ROOT = Path("/home/king/rebuild/source/resources")


def _typical_packs() -> list[dict]:
    return [s for s in list_scenarios()["scenarios"] if s["tier"] == TIER_TYPICAL]


def test_three_typical_packs_exist_with_all_four_files(isolated_data):
    """R20-3-04 硬门槛：三个典型包各四文件齐备。"""
    packs = _typical_packs()
    ids = {p["scenario_id"] for p in packs}
    assert {"xinchuang_switch", "modernization", "porting"} <= ids, ids
    for p in packs:
        assert p["files_present"] == {"skill": True, "anchors": True, "risks": True, "meta": True}, \
            f"{p['scenario_id']} 四文件不齐：{p['files_present']}"
        assert p["metrics"]["files_present_all"] is True


def test_generic_pack_is_deliberately_thin(isolated_data):
    """`_generic` 是厚度基准的分母：把它灌厚会同时抬高典型包门槛并违背兜底定位。"""
    g = resolve_scenario_pack(None)
    assert g["scenario_id"] == GENERIC_SCENARIO_DIR
    m = g["metrics"]
    assert m["candidate_count"] == 0, "兜底包不得预设任何目标技术栈候选"
    assert m["risk_count"] > 0 and m["anchor_count"] > 0, "兜底包仍须给出少量通用条目"
    assert m["risk_count"] <= 5 and m["anchor_count"] <= 5, \
        f"兜底包过厚（risk={m['risk_count']} anchor={m['anchor_count']}），会抬高典型包门槛"


@pytest.mark.parametrize("metric", ["risk_count", "anchor_count", "skill_body_chars"])
def test_typical_packs_are_thicker_than_generic(isolated_data, metric):
    """主指标 + 弱辅助指标：典型包须达 _generic 的 THICKNESS_RATIO 倍。"""
    generic = resolve_scenario_pack(None)["metrics"]
    floor = THICKNESS_RATIO * generic[metric]
    for p in _typical_packs():
        actual = p["metrics"][metric]
        assert actual >= floor, (
            f"{p['scenario_id']}.{metric} = {actual} < {floor}"
            f"（_generic = {generic[metric]} × THICKNESS_RATIO {THICKNESS_RATIO}）"
            " —— 应补真实内容，不得调低阈值凑过")


def test_typical_packs_have_enough_selection_candidates(isolated_data):
    """主指标（最难灌水）：每个候选须是真实存在的产品/目标态，凑不出来。"""
    for p in _typical_packs():
        n = p["metrics"]["candidate_count"]
        assert n >= MIN_TYPICAL_CANDIDATES, \
            f"{p['scenario_id']} 选型候选 {n} < {MIN_TYPICAL_CANDIDATES}"
        for cand in p["selection_candidates"]:
            assert isinstance(cand, dict) and str(cand.get("name", "")).strip(), \
                f"{p['scenario_id']} 存在无名候选：{cand}"


def test_typical_packs_have_enough_resource_refs(isolated_data):
    for p in _typical_packs():
        n = p["metrics"]["resource_ref_count"]
        assert n >= MIN_TYPICAL_RESOURCE_REFS, \
            f"{p['scenario_id']} 资源引用 {n} < {MIN_TYPICAL_RESOURCE_REFS}"


def test_resource_refs_point_at_things_that_really_exist(isolated_data):
    """辅指标须可自动校验存在性 —— 否则引用数可以靠写假名字刷高。"""
    known_skills = {d.name for cat in SKILLS_ROOT.iterdir() if cat.is_dir()
                    for d in cat.iterdir() if d.is_dir()}
    known_resources = ({d.name for d in RESOURCES_ROOT.iterdir() if d.is_dir()}
                       if RESOURCES_ROOT.is_dir() else set())
    for p in _typical_packs() + [resolve_scenario_pack(None)]:
        for name in p["related_skills"]:
            assert name in known_skills, \
                f"{p['scenario_id']}.related_skills 引用了不存在的 skill：{name}"
        for name in p["related_resources"]:
            assert name in known_resources, \
                f"{p['scenario_id']}.related_resources 引用了不存在的资源：{name}"


def test_typical_pack_risks_are_specific_not_boilerplate(isolated_data):
    """R20-3-07 的可自动化部分：风险条目须带核实方式，不是"注意兼容性"式套话。

    完整的内容质量判定无法自动化（须人工抽查），此处只把最低限度的具体性固化。
    """
    for p in _typical_packs():
        lines = [l.strip() for l in p["risks_text"].splitlines() if l.strip().startswith("-")]
        assert lines, p["scenario_id"]
        with_verification = [l for l in lines if "核实" in l]
        assert len(with_verification) == len(lines), (
            f"{p['scenario_id']} 有 {len(lines) - len(with_verification)} 条风险未给核实方式")
        short = [l for l in lines if len(l) < 25]
        assert not short, f"{p['scenario_id']} 存在过短的套话风险条目：{short}"


def test_metrics_are_exposed_for_independent_review(isolated_data):
    """验收方须能不数条目就复核：manifest.metrics 经 list_scenarios / API 直接可读。"""
    for p in _typical_packs():
        assert set(p["metrics"]) == {
            "candidate_count", "risk_count", "anchor_count",
            "resource_ref_count", "skill_body_chars", "files_present_all",
        }, p["metrics"]


def test_thickness_measured_against_the_real_disk_tree(isolated_data):
    """本文件的断言必须打在真实包目录上（不是 tmp 造的假包）。"""
    root = scenarios_root()
    assert root == Path("/home/king/rebuild/source/skills/scenarios"), root
    for sid in ("_generic", "xinchuang_switch", "modernization", "porting"):
        assert (root / sid / "SKILL.md").is_file(), sid
