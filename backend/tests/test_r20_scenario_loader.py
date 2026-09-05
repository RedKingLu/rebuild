"""R20-3 场景包机制 —— scenario_loader 发现 / 兜底 / 路径安全 / 改写即生效 / 真注入.

测试纪律（③§9.4）：断言必须打在**真实包内容、真实 manifest 数值、真实 prompt 文本**上。
禁止只断言"模块可导入 / 函数可调用 / 目录 exists"—— 那是 AGENTS §10-17（写了接口当已联调）
在测试层的表现。

隔离说明：需要**改写/新建**场景包的用例一律 monkeypatch `SKILL_SOURCE_ROOT` 指向 tmp，
绝不写真实仓库的 `source/skills/scenarios/`；断言真实内置包的用例则读真实根
（conftest.py:166-167 已把 SKILL_SOURCE_ROOT 指回真实 skills 目录）。
"""

from __future__ import annotations

from pathlib import Path

from app.services.context_layers import assemble_c1
from app.services.scenario_loader import (
    GENERIC_SCENARIO_DIR,
    SC_GENERIC_UNAVAILABLE,
    SC_ID_INVALID,
    SC_META_INVALID_YAML,
    SC_META_UNKNOWN_KEYS,
    SC_NOT_SELECTED,
    SC_PACK_NOT_FOUND,
    SC_ROOT_MISSING,
    SC_SKILL_MISSING,
    SCENARIOS_DIR_NAME,
    TIER_OPEN,
    TIER_TYPICAL,
    list_scenarios,
    resolve_scenario_pack,
    scenarios_root,
)

REPO_ROOT = Path("/home/king/rebuild")

_MIN_FRONTMATTER = "---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n"


def _tmp_scenarios_root(tmp_path: Path, monkeypatch, with_generic: bool = True) -> Path:
    """把场景根指向 tmp（写隔离），并按需放一个可用的兜底包。返回 scenarios 目录。"""
    skills = tmp_path / "skills"
    scenarios = skills / SCENARIOS_DIR_NAME
    scenarios.mkdir(parents=True)
    if with_generic:
        gdir = scenarios / GENERIC_SCENARIO_DIR
        gdir.mkdir()
        (gdir / "SKILL.md").write_text(
            _MIN_FRONTMATTER.format(
                name=GENERIC_SCENARIO_DIR, desc="兜底",
                body="通用兜底方法论：无专用场景知识，不预设任何目标技术栈。"),
            encoding="utf-8")
    monkeypatch.setenv("SKILL_SOURCE_ROOT", str(skills))
    return scenarios


def _write_pack(scenarios: Path, name: str, body: str, meta: str | None = None,
                anchors: str | None = None, risks: str | None = None) -> Path:
    d = scenarios / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        _MIN_FRONTMATTER.format(name=name, desc="测试场景包", body=body), encoding="utf-8")
    if meta is not None:
        (d / "meta.yaml").write_text(meta, encoding="utf-8")
    if anchors is not None:
        (d / "acceptance-anchors.md").write_text(anchors, encoding="utf-8")
    if risks is not None:
        (d / "risk-catalog.md").write_text(risks, encoding="utf-8")
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 真实内置包：发现 + 真实内容
# ─────────────────────────────────────────────────────────────────────────────

def test_scenarios_root_is_derived_from_skill_source_root(isolated_data):
    """根解析必须与 skill_loader 同源，否则测试期落到不存在的 <tmp>/source/skills/scenarios。"""
    root = scenarios_root()
    assert root.name == SCENARIOS_DIR_NAME
    assert root.parent.name == "skills"
    assert root.is_dir(), f"场景包根不存在：{root}"


def test_list_scenarios_finds_three_typical_packs(isolated_data):
    """真实磁盘上三个典型包可被发现，且 tier 由 meta.yaml 真实声明。"""
    result = list_scenarios()
    assert result["discovery_status"] == "ok", result
    assert result["invalid"] == [], f"存在非法场景包目录：{result['invalid']}"
    ids = {s["scenario_id"] for s in result["scenarios"]}
    assert {"xinchuang_switch", "modernization", "porting"} <= ids, ids
    # 兜底包不作为用户可选场景出现在列表里
    assert GENERIC_SCENARIO_DIR not in ids
    by_id = {s["scenario_id"]: s for s in result["scenarios"]}
    for sid in ("xinchuang_switch", "modernization", "porting"):
        assert by_id[sid]["tier"] == TIER_TYPICAL, sid
        assert by_id[sid]["capability_status"] == "real", sid
        assert by_id[sid]["metrics"]["files_present_all"] is True, sid


def test_typical_packs_carry_real_technical_facts(isolated_data):
    """R20-3-07：断言真实技术事实出现在真实包正文里，不是"文件存在"。"""
    xc = resolve_scenario_pack("xinchuang_switch")
    assert xc["fallback"] is None
    # R20-3-05 明文要求信创包保留三候选
    names = " ".join(str(c) for c in xc["selection_candidates"])
    for cand in ("达梦", "人大金仓", "openGauss"):
        assert cand in names, f"信创包选型候选缺少 {cand}：{names}"
    assert "达梦" in xc["skill_body"] and "麒麟" in xc["skill_body"]

    mo = resolve_scenario_pack("modernization")
    assert mo["fallback"] is None
    for fact in ("System.Web", "BinaryFormatter", "jakarta"):
        assert fact in mo["skill_body"], f"modernization 正文缺少具体事实 {fact}"
    # R20-4-02：现代化场景须显式禁止产出国产化替代建议
    assert "国产化替代" in mo["skill_body"]

    po = resolve_scenario_pack("porting")
    assert po["fallback"] is None
    for fact in ("大小写", "字节序", "JNI"):
        assert fact in po["skill_body"], f"porting 正文缺少具体事实 {fact}"


def test_generic_pack_honestly_declares_no_scenario_knowledge(isolated_data):
    """兜底包须诚实标注无专用场景知识，且自身不含任何具体场景口径（R20-3-03）。"""
    pack = resolve_scenario_pack(None)
    assert pack["scenario_id"] == GENERIC_SCENARIO_DIR
    assert pack["fallback"]["code"] == SC_NOT_SELECTED
    assert "无专用场景知识" in pack["skill_body"]
    # 兜底包不得静默套用信创口径 —— 否则未选场景的项目仍被灌信创
    for word in ("信创", "达梦", "人大金仓", "openGauss", "麒麟", "统信"):
        assert word not in pack["skill_body"], f"兜底包正文出现封闭场景口径：{word}"
    # tier 缺省不得上调为 typical
    assert pack["tier"] == TIER_OPEN


def test_c1_injects_scenario_pack_content(isolated_data):
    """真注入：锚点与风险的【内容】须进 prompt，不是只有条数（②推翻 2）。"""
    pack = resolve_scenario_pack("modernization")
    assert pack["anchors_text"] and pack["risks_text"]
    layer = assemble_c1(scenario_pack=pack)
    content = layer["content"]
    assert pack["anchors_text"][:40] in content
    assert pack["risks_text"][:40] in content
    assert pack["skill_body"][:40] in content
    assert pack["display_name"] in content
    # 装配链路自身不得残留无条件信创口径
    assert "信创" not in content


def test_c1_without_scenario_is_honest_and_scenario_neutral(isolated_data):
    """未提供场景时须诚实标注，且不得假设任何目标技术栈。"""
    content = assemble_c1()["content"]
    assert "未提供场景信息" in content
    assert "不得假设任何目标技术栈" in content
    assert "信创" not in content


def test_xinchuang_pack_content_reaches_c1_layer(isolated_data):
    """R20-3-06：信创项目的场景知识须真实进入 C1（改造后不退化，只是改由场景包供给）。"""
    content = assemble_c1(scenario_pack=resolve_scenario_pack("xinchuang_switch"))["content"]
    for word in ("达梦", "人大金仓", "openGauss", "麒麟"):
        assert word in content, f"信创场景装配后 C1 缺少 {word}"


# ─────────────────────────────────────────────────────────────────────────────
# 兜底四路径 + 路径安全（公理3：每条都带 (code, message)）
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_when_not_selected(isolated_data, tmp_path, monkeypatch):
    _tmp_scenarios_root(tmp_path, monkeypatch)
    for value in ("", "   ", None):
        pack = resolve_scenario_pack(value)
        assert pack["fallback"]["code"] == SC_NOT_SELECTED, value
        assert pack["fallback"]["message"], "message 不得为空（人与模型都要读）"


def test_fallback_when_pack_missing(isolated_data, tmp_path, monkeypatch):
    _tmp_scenarios_root(tmp_path, monkeypatch)
    pack = resolve_scenario_pack("this_scenario_does_not_exist")
    assert pack["fallback"]["code"] == SC_PACK_NOT_FOUND
    assert pack["scenario_id"] == GENERIC_SCENARIO_DIR


def test_fallback_when_pack_has_no_skill_md(isolated_data, tmp_path, monkeypatch):
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    (scenarios / "empty_pack").mkdir()
    pack = resolve_scenario_pack("empty_pack")
    assert pack["fallback"]["code"] == SC_SKILL_MISSING
    # 目录不删不改
    assert (scenarios / "empty_pack").is_dir()


def test_invalid_pack_is_registered_not_silently_skipped(isolated_data, tmp_path, monkeypatch):
    """缺 SKILL.md 的目录必须进 invalid[] 并带 (code, message)，不得静默跳过。"""
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    (scenarios / "broken_pack").mkdir()
    _write_pack(scenarios, "good_pack", "可用场景包正文")
    result = list_scenarios()
    ids = {s["scenario_id"] for s in result["scenarios"]}
    assert ids == {"good_pack"}
    assert len(result["invalid"]) == 1
    assert result["invalid"][0]["dir"] == "broken_pack"
    assert result["invalid"][0]["code"] == SC_SKILL_MISSING
    assert result["invalid"][0]["message"]


def test_root_missing_reports_honestly(isolated_data, tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_SOURCE_ROOT", str(tmp_path / "nowhere"))
    result = list_scenarios()
    assert result["discovery_status"] == "root_missing"
    assert result["scenarios"] == []
    assert result["notices"][0]["code"] == SC_ROOT_MISSING
    assert str(tmp_path) in result["root"], "已解析的绝对路径须回报，便于诊断"


def test_path_traversal_and_bad_shapes_are_rejected(isolated_data, tmp_path, monkeypatch):
    """路径安全按 R18-1 HIGH-01 同级对待：形状白名单 + 归属校验，不可绕过。"""
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    # 在根之外放一个"看起来像场景包"的目录，证明穿越拿不到它
    outside = tmp_path / "outside_pack"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        _MIN_FRONTMATTER.format(name="x", desc="x", body="OUTSIDE_SENTINEL"), encoding="utf-8")

    for bad in ("../outside_pack", "../../outside_pack", "/etc", "..", ".",
                "a/b", "a\\b", "_generic", "_internal", "", "x" * 65):
        pack = resolve_scenario_pack(bad)
        assert pack["fallback"] is not None, bad
        assert pack["fallback"]["code"] in (SC_ID_INVALID, SC_NOT_SELECTED,
                                            SC_PACK_NOT_FOUND), (bad, pack["fallback"])
        assert "OUTSIDE_SENTINEL" not in pack["skill_body"], f"路径穿越成功：{bad}"
    assert scenarios.is_dir()


def test_symlink_escaping_root_is_rejected(isolated_data, tmp_path, monkeypatch):
    """归属校验用 resolve() + is_relative_to()，符号链接指向根外须被拒。"""
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    outside = tmp_path / "outside_real"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        _MIN_FRONTMATTER.format(name="x", desc="x", body="SYMLINK_SENTINEL"), encoding="utf-8")
    (scenarios / "sneaky").symlink_to(outside, target_is_directory=True)
    pack = resolve_scenario_pack("sneaky")
    assert pack["fallback"] is not None
    assert "SYMLINK_SENTINEL" not in pack["skill_body"]


def test_shape_check_is_shape_not_value_whitelist(isolated_data, tmp_path, monkeypatch):
    """形状白名单只约束字符集，不约束取值集合 —— 任意新场景 id 均须可用（AGENTS §10-27）。"""
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    for sid in ("cloud_native", "Legacy.Modernize-v2", "a", "team42"):
        _write_pack(scenarios, sid, f"SENTINEL_FOR_{sid}")
        pack = resolve_scenario_pack(sid)
        assert pack["fallback"] is None, sid
        assert f"SENTINEL_FOR_{sid}" in pack["skill_body"], sid


def test_generic_unavailable_never_compensates_with_typical_pack(
        isolated_data, tmp_path, monkeypatch):
    """兜底包自身不可用时：无场景文本 + 明确上报，【绝不】代偿为任何典型包。"""
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch, with_generic=False)
    _write_pack(scenarios, "some_typical", "TYPICAL_SENTINEL_MUST_NOT_LEAK",
                meta="tier: typical\ndisplay_name: 某典型场景\n")
    pack = resolve_scenario_pack("no_such_pack")
    assert pack["fallback"]["code"] == SC_GENERIC_UNAVAILABLE
    assert pack["skill_body"] == ""
    assert "TYPICAL_SENTINEL_MUST_NOT_LEAK" not in str(pack)
    assert pack["capability_status"] == "not_connected"
    # 渲染出的 C1 仍须诚实，不得凭空产生场景知识
    content = assemble_c1(scenario_pack=pack)["content"]
    assert SC_GENERIC_UNAVAILABLE in content
    assert "TYPICAL_SENTINEL_MUST_NOT_LEAK" not in content


# ─────────────────────────────────────────────────────────────────────────────
# meta.yaml 行为（未知键发声、非法 YAML 不使整包失效、tier 不上调）
# ─────────────────────────────────────────────────────────────────────────────

def test_meta_invalid_yaml_pack_still_usable_and_reports(isolated_data, tmp_path, monkeypatch):
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    _write_pack(scenarios, "bad_meta", "BAD_META_BODY_SENTINEL",
                meta="tier: [unclosed\n  : :\n")
    pack = resolve_scenario_pack("bad_meta")
    assert pack["fallback"] is None, "SKILL.md 才是实质，meta 非法不应使整包失效"
    assert "BAD_META_BODY_SENTINEL" in pack["skill_body"]
    assert pack["tier"] == TIER_OPEN, "tier 须回落 open，绝不上调 typical"
    codes = [n["code"] for n in pack["notices"]]
    assert SC_META_INVALID_YAML in codes, codes


def test_meta_unknown_keys_warn_but_do_not_fail(isolated_data, tmp_path, monkeypatch, caplog):
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    _write_pack(scenarios, "typo_meta", "TYPO_META_BODY",
                meta="tier: typical\ndispaly_name: 拼错的键\nsummry: 也拼错了\n")
    with caplog.at_level("WARNING"):
        pack = resolve_scenario_pack("typo_meta")
    assert pack["fallback"] is None
    assert pack["tier"] == TIER_TYPICAL
    notice = [n for n in pack["notices"] if n["code"] == SC_META_UNKNOWN_KEYS]
    assert notice, pack["notices"]
    assert "dispaly_name" in notice[0]["message"] and "summry" in notice[0]["message"]
    assert any("unknown top-level keys" in r.message for r in caplog.records), \
        "未知键必须 logger.warning 发声，不得静默忽略"
    # display_name 拼错 → 回落目录名，不是静默空串
    assert pack["display_name"] == "typo_meta"


def test_meta_top_level_not_mapping_falls_back_to_open(isolated_data, tmp_path, monkeypatch):
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    _write_pack(scenarios, "list_meta", "LIST_META_BODY", meta="- a\n- b\n")
    pack = resolve_scenario_pack("list_meta")
    assert pack["fallback"] is None
    assert pack["tier"] == TIER_OPEN
    assert "scenario-meta-invalid-shape" in [n["code"] for n in pack["notices"]]


def test_tier_typo_is_never_upgraded_to_typical(isolated_data, tmp_path, monkeypatch):
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    for bad_tier in ("Typical", "TYPICAL", "premium", "1"):
        _write_pack(scenarios, "t_pack", "BODY", meta=f"tier: {bad_tier}\n")
        assert resolve_scenario_pack("t_pack")["tier"] == TIER_OPEN, bad_tier


def test_optional_files_absent_is_explicitly_stated(isolated_data, tmp_path, monkeypatch):
    """锚点/风险缺失时包仍合法，但注入文本须显式写明"未提供"（不得当空字符串静默）。"""
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    _write_pack(scenarios, "bare_pack", "BARE_BODY")
    pack = resolve_scenario_pack("bare_pack")
    assert pack["fallback"] is None
    assert pack["files_present"] == {"skill": True, "anchors": False,
                                     "risks": False, "meta": False}
    codes = [n["code"] for n in pack["notices"]]
    assert "scenario-anchors-absent" in codes and "scenario-risks-absent" in codes
    content = assemble_c1(scenario_pack=pack)["content"]
    assert "本场景包未提供验收锚点" in content
    assert "本场景包未提供风险清单" in content


def test_oversized_text_is_truncated_with_visible_marker(isolated_data, tmp_path, monkeypatch):
    """截断是信息丢失，须发声（manifest 标记 + 注入文本末尾标注）。"""
    from app.services.context_layers import SCENARIO_RISKS_MAX_CHARS
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    long_risks = "\n".join(f"- 风险条目 {i} " + "补" * 40
                           for i in range(SCENARIO_RISKS_MAX_CHARS))
    _write_pack(scenarios, "big_pack", "BODY", risks=long_risks)
    pack = resolve_scenario_pack("big_pack")
    assert pack["risks_truncated"] is True
    assert len(pack["risks_text"]) == SCENARIO_RISKS_MAX_CHARS
    content = assemble_c1(scenario_pack=pack)["content"]
    assert "本节内容已截断" in content
    assert "risk-catalog.md" in content


# ─────────────────────────────────────────────────────────────────────────────
# R20-3-02 改写即生效 / R20-3-01 零代码新增场景（结构性判据）
# ─────────────────────────────────────────────────────────────────────────────

def test_rewrite_takes_effect_without_restart(isolated_data, tmp_path, monkeypatch):
    """R20-3-02 的结构性证明：无缓存 ⇒ 改写后下次解析即读到新内容。

    若日后有人加了内容缓存，本用例立刻变红。
    """
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    pack_dir = _write_pack(scenarios, "editable", "SENTINEL_S1_ORIGINAL")
    assert "SENTINEL_S1_ORIGINAL" in resolve_scenario_pack("editable")["skill_body"]

    # 不重启、不清任何缓存，直接改写文件
    (pack_dir / "SKILL.md").write_text(
        _MIN_FRONTMATTER.format(name="editable", desc="改写后", body="SENTINEL_S2_REWRITTEN"),
        encoding="utf-8")
    body = resolve_scenario_pack("editable")["skill_body"]
    assert "SENTINEL_S2_REWRITTEN" in body
    assert "SENTINEL_S1_ORIGINAL" not in body

    # 改写后的内容必须真的进到装配出的 C1 文本里
    assert "SENTINEL_S2_REWRITTEN" in assemble_c1(
        scenario_pack=resolve_scenario_pack("editable"))["content"]


def test_new_scenario_needs_no_code_change(isolated_data, tmp_path, monkeypatch):
    """R20-3-01（后端链路部分）：只 mkdir + 写 SKILL.md，loader→装配链路即可发现并注入。

    ⚠️ 边界：R20-3-01 完整判据还要求"前端可见 + 建项目可选中"，那依赖 R20-2 的
    Project.scenario 字段与 OnboardingWizard 接线。本用例只覆盖后端链路，
    不得用它冒充完整判据（AGENTS §10-17）。
    """
    scenarios = _tmp_scenarios_root(tmp_path, monkeypatch)
    _write_pack(scenarios, "existing_one", "EXISTING_BODY")
    before = {s["scenario_id"] for s in list_scenarios()["scenarios"]}
    assert "cloud_native" not in before

    _write_pack(scenarios, "cloud_native", "CLOUD_NATIVE_SENTINEL",
                meta="display_name: 云原生化\nsummary: 上云与云原生改造\n")

    after = {s["scenario_id"] for s in list_scenarios()["scenarios"]}
    assert after == before | {"cloud_native"}

    pack = resolve_scenario_pack("cloud_native")
    assert pack["capability_status"] == "real"
    assert pack["fallback"] is None
    assert "CLOUD_NATIVE_SENTINEL" in pack["skill_body"]
    assert pack["display_name"] == "云原生化"
    assert pack["tier"] == TIER_OPEN, "未声明 tier 的用户扩展场景按 open 处理"

    content = assemble_c1(scenario_pack=pack)["content"]
    assert "CLOUD_NATIVE_SENTINEL" in content
    assert "云原生化" in content


# ─────────────────────────────────────────────────────────────────────────────
# 装配链路贯通 + API
# ─────────────────────────────────────────────────────────────────────────────

def test_assemble_context_carries_scenario_into_prompt_and_trace(isolated_data):
    """场景值须能经 project dict → C1 场景块 + 身份句 + trace 全程贯通。"""
    from app.services.context_assembler import assemble_context, build_system_prompt
    ctx = assemble_context("proj-scn-1", "p1",
                           project={"name": "现代化样本", "scenario": "modernization"},
                           task_type="default")
    trace = ctx["assembly_trace"]
    assert trace["scenario"] == "modernization"
    assert trace["scenario_status"] == "ok"
    assert trace["scenario_source"] == "project_dict"
    assert trace["scenario_tier"] == TIER_TYPICAL
    assert trace["scenario_chars"]["skill_body"] > 0
    # trace 不得携带正文原文（体积 + 脱敏）
    assert "System.Web" not in str(trace)
    assert ctx["project"]["scenario"] == "modernization"

    prompt = build_system_prompt("proj-scn-1", "p1",
                                 project={"name": "现代化样本", "scenario": "modernization"},
                                 task_type="default")
    assert "软件现代化" in prompt
    assert "System.Web" in prompt, "场景包正文须真实进入 system_prompt"


def test_assemble_context_flags_missing_scenario_key(isolated_data):
    """调用方 project dict 缺 scenario 键时，须在 trace 里显性可见（scenario_source=absent）。"""
    from app.services.context_assembler import assemble_context
    ctx = assemble_context("proj-scn-2", "p1", project={"name": "无场景键"}, task_type="default")
    trace = ctx["assembly_trace"]
    assert trace["scenario_source"] == "absent"
    assert trace["scenario_status"] == SC_NOT_SELECTED
    assert trace["scenario"] == GENERIC_SCENARIO_DIR


def test_scenarios_api_lists_real_packs(client):
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = {s["scenario_id"] for s in data["scenarios"]}
    assert {"xinchuang_switch", "modernization", "porting"} <= ids, ids
    assert data["discovery_status"] == "ok"


def test_scenarios_api_returns_real_pack_content(client):
    resp = client.get("/api/scenarios/xinchuang_switch")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tier"] == TIER_TYPICAL
    assert "达梦" in data["skill_body"]
    assert data["anchors_text"] and data["risks_text"]
    assert data["metrics"]["candidate_count"] >= 3


def test_scenarios_api_unknown_id_is_404_without_path_leak(client):
    resp = client.get("/api/scenarios/definitely_not_a_scenario")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == SC_PACK_NOT_FOUND
    assert "/home/king" not in str(detail), "错误响应不得泄露落盘路径细节"


def test_scenarios_api_rejects_traversal(client):
    resp = client.get("/api/scenarios/..%2F..%2Fetc")
    assert resp.status_code in (400, 404), resp.status_code
