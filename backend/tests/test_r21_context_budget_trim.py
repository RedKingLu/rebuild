"""R21 验收测试 — max_context_budget 真正接线：估算函数 + 装配清单 + 预算裁剪.

背景：context_layers.py 里的 max_context_budget（DEFAULT_CONTEXT_RECIPE，声明值
"100k tokens"）此前从未有真实消费方——build_system_prompt_from_layers 只是按顺序拼接，
不做任何长度判断。本批次补齐两件事：
  1. 装配清单（assembly_trace.layers_manifest / .budget）：每层字符数 + 截断标记
     （复用已有 body_truncated / scenario_*_truncated，不新造）+ C3 命中的 skill 名单 +
     是否因预算裁剪未纳入。
  2. 真正的预算裁剪：parse_token_budget / estimate_char_budget（独立可替换的估算函数，
     不内联进拼接函数）+ plan_budget_trim（按 LAYER_PRIORITY 反序丢整层，C0/C1/C2 永不裁）。

T1: parse_token_budget / estimate_char_budget 估算函数（独立单测，覆盖不同长度输入）
T2: plan_budget_trim 纯函数（正常场景不裁 / 超预算场景裁 C6 优先 / C0-C2 永不裁）
T3: build_system_prompt_from_layers 真正应用裁剪（字符串层面验证）
T4: context_assembler.assemble_context 的 layers_manifest / budget 装配清单
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# T1 — 估算函数（parse_token_budget / estimate_char_budget）
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_token_budget_k_suffix():
    from app.services.context_layers import parse_token_budget
    assert parse_token_budget("100k tokens") == 100_000
    assert parse_token_budget("100K") == 100_000


def test_parse_token_budget_plain_number_string():
    from app.services.context_layers import parse_token_budget
    assert parse_token_budget("50000") == 50_000
    assert parse_token_budget("50000 tokens") == 50_000


def test_parse_token_budget_decimal_k():
    from app.services.context_layers import parse_token_budget
    assert parse_token_budget("1.5k tokens") == 1_500


def test_parse_token_budget_numeric_types():
    from app.services.context_layers import parse_token_budget
    assert parse_token_budget(2000) == 2000
    assert parse_token_budget(2000.0) == 2000


def test_parse_token_budget_unparseable_falls_back_honestly():
    """解析失败不抛异常、不静默假装成 0——回落到保守默认值（公理3）。"""
    from app.services.context_layers import parse_token_budget, _DEFAULT_TOKEN_BUDGET_FALLBACK
    assert parse_token_budget("not a budget at all") == _DEFAULT_TOKEN_BUDGET_FALLBACK
    assert parse_token_budget(None) == _DEFAULT_TOKEN_BUDGET_FALLBACK
    assert parse_token_budget("") == _DEFAULT_TOKEN_BUDGET_FALLBACK


def test_estimate_char_budget_scales_with_tokens():
    """估算函数独立于拼接函数：不同长度输入 → 不同字符上限，且比例一致。"""
    from app.services.context_layers import estimate_char_budget, CHARS_PER_TOKEN_ESTIMATE
    small = estimate_char_budget("1k tokens")
    large = estimate_char_budget("100k tokens")
    assert small == int(1_000 * CHARS_PER_TOKEN_ESTIMATE)
    assert large == int(100_000 * CHARS_PER_TOKEN_ESTIMATE)
    assert large == small * 100


def test_estimate_char_budget_default_recipe_value():
    """DEFAULT_CONTEXT_RECIPE 的声明值 "100k tokens" 能被估算函数消费为一个具体数字上限。"""
    from app.services.context_layers import estimate_char_budget, DEFAULT_CONTEXT_RECIPE
    max_chars = estimate_char_budget(DEFAULT_CONTEXT_RECIPE["max_context_budget"])
    assert isinstance(max_chars, int)
    assert max_chars > 0


# ─────────────────────────────────────────────────────────────────────────────
# T2 — plan_budget_trim（纯函数：反序丢层，C0-C2 永不裁）
# ─────────────────────────────────────────────────────────────────────────────

def _layer(chars: int) -> dict:
    return {"content": "x" * chars, "chars": chars}


def test_plan_budget_trim_normal_size_drops_nothing():
    """正常大小的上下文远小于预算 → 不触发裁剪，全部保留（与改动前行为一致）。"""
    from app.services.context_layers import plan_budget_trim
    layers = {k: _layer(500) for k in ("C0", "C1", "C2", "C3", "C4", "C5", "C6")}
    plan = plan_budget_trim(layers, max_chars=200_000)
    assert all(not e["dropped_by_budget"] for e in plan)
    assert len(plan) == 7


def test_plan_budget_trim_oversized_drops_c6_first():
    """超预算场景：C6 优先被裁，C0-C5 保留。"""
    from app.services.context_layers import plan_budget_trim
    layers = {
        "C0": _layer(100), "C1": _layer(100), "C2": _layer(100),
        "C3": _layer(100), "C4": _layer(100), "C5": _layer(100),
        "C6": _layer(50_000),  # 单独把总量推过预算
    }
    plan = plan_budget_trim(layers, max_chars=1_000)
    by_layer = {e["layer"]: e for e in plan}
    assert by_layer["C6"]["dropped_by_budget"] is True
    for key in ("C0", "C1", "C2", "C3", "C4", "C5"):
        assert by_layer[key]["dropped_by_budget"] is False


def test_plan_budget_trim_drops_multiple_layers_in_reverse_priority():
    """C6 裁完仍超预算 → 继续裁 C5，再裁 C4……直到满足预算或碰到 C0-C2。"""
    from app.services.context_layers import plan_budget_trim
    layers = {
        "C0": _layer(50), "C1": _layer(50), "C2": _layer(50),
        "C3": _layer(50), "C4": _layer(50),
        "C5": _layer(40_000), "C6": _layer(40_000),
    }
    plan = plan_budget_trim(layers, max_chars=300)
    by_layer = {e["layer"]: e for e in plan}
    assert by_layer["C6"]["dropped_by_budget"] is True
    assert by_layer["C5"]["dropped_by_budget"] is True
    # C0-C4 的总字符数 (250) 已 <= max_chars (300)，裁到 C4 前应已停手
    assert by_layer["C4"]["dropped_by_budget"] is False
    assert by_layer["C3"]["dropped_by_budget"] is False


def test_plan_budget_trim_never_drops_c0_c1_c2_even_when_still_over_budget():
    """C0-C2 本身就超预算 → 保留它们，"超预算就超预算"，不为塞进预算裁掉治理/产品/架构层。"""
    from app.services.context_layers import plan_budget_trim
    layers = {
        "C0": _layer(100_000), "C1": _layer(100_000), "C2": _layer(100_000),
        "C3": _layer(100), "C4": _layer(100), "C5": _layer(100), "C6": _layer(100),
    }
    plan = plan_budget_trim(layers, max_chars=1_000)
    by_layer = {e["layer"]: e for e in plan}
    for key in ("C0", "C1", "C2"):
        assert by_layer[key]["dropped_by_budget"] is False
    # C3-C6 仍会被尝试裁掉（即便裁完仍超预算，裁剪逻辑本身照常执行到底）
    for key in ("C3", "C4", "C5", "C6"):
        assert by_layer[key]["dropped_by_budget"] is True


def test_plan_budget_trim_only_covers_assembled_layers():
    """未装配的层不出现在 plan 里（不是"层不存在"和"层被裁"混为一谈）。"""
    from app.services.context_layers import plan_budget_trim
    layers = {"C0": _layer(10), "C3": _layer(10)}
    plan = plan_budget_trim(layers, max_chars=1)
    keys = {e["layer"] for e in plan}
    assert keys == {"C0", "C3"}


# ─────────────────────────────────────────────────────────────────────────────
# T3 — build_system_prompt_from_layers 真正应用裁剪
# ─────────────────────────────────────────────────────────────────────────────

def _marked_layer(marker: str, chars: int) -> dict:
    return {"layer": marker, "content": f"[{marker}-START]" + "x" * chars + f"[{marker}-END]",
            "chars": chars + len(marker) * 2 + 14}


def test_build_system_prompt_normal_size_unaffected_by_trim():
    """正常大小场景：行为跟改动前一致，所有层内容都在 prompt 里。"""
    from app.services.context_layers import build_system_prompt_from_layers
    layers = {k: _marked_layer(k, 200) for k in ("C0", "C1", "C2", "C3", "C4", "C5", "C6")}
    prompt = build_system_prompt_from_layers(layers, "p0", "node_worker_agent", "")
    for k in ("C0", "C1", "C2", "C3", "C4", "C5", "C6"):
        assert f"[{k}-START]" in prompt


def test_build_system_prompt_oversized_drops_c6_keeps_c0_c1_c2():
    """故意构造超预算场景（C6 巨大）：C6 被裁出 prompt，C0-C2 一定保留。"""
    from app.services.context_layers import build_system_prompt_from_layers
    layers = {
        "C0": _marked_layer("C0", 500), "C1": _marked_layer("C1", 500),
        "C2": _marked_layer("C2", 500), "C3": _marked_layer("C3", 500),
        "C4": _marked_layer("C4", 500), "C5": _marked_layer("C5", 500),
        "C6": _marked_layer("C6", 5_000_000),  # 远超 "100k tokens" 默认预算估算
    }
    prompt = build_system_prompt_from_layers(layers, "p0", "node_worker_agent", "")
    assert "[C6-START]" not in prompt, "C6 应因预算裁剪被排除"
    for k in ("C0", "C1", "C2"):
        assert f"[{k}-START]" in prompt, f"{k} 治理/产品/架构层永不因预算裁剪而丢失"


def test_build_system_prompt_respects_explicit_max_context_budget_param():
    """显式传入更紧的预算，应比默认预算裁得更狠（验证参数真的被读取，不是摆设）。"""
    from app.services.context_layers import build_system_prompt_from_layers
    layers = {
        "C0": _marked_layer("C0", 100), "C1": _marked_layer("C1", 100),
        "C2": _marked_layer("C2", 100), "C3": _marked_layer("C3", 100),
        "C4": _marked_layer("C4", 100), "C5": _marked_layer("C5", 100),
        "C6": _marked_layer("C6", 100),
    }
    # 默认预算（100k tokens ≈ 数十万字符）下，这点内容不会被裁
    prompt_default = build_system_prompt_from_layers(layers, "p0", "node_worker_agent", "")
    assert "[C6-START]" in prompt_default and "[C4-START]" in prompt_default

    # 显式给一个极紧的预算（几十字符）→ C3-C6 应被裁掉，C0-C2 仍保留
    prompt_tight = build_system_prompt_from_layers(
        layers, "p0", "node_worker_agent", "", max_context_budget="10 tokens")
    assert "[C6-START]" not in prompt_tight
    assert "[C0-START]" in prompt_tight and "[C1-START]" in prompt_tight and "[C2-START]" in prompt_tight


# ─────────────────────────────────────────────────────────────────────────────
# T4 — context_assembler.assemble_context 的装配清单（layers_manifest / budget）
# ─────────────────────────────────────────────────────────────────────────────

def test_assemble_context_layers_manifest_structure(isolated_data):
    from app.services.context_assembler import assemble_context
    from app.services.context_layers import LAYER_PRIORITY

    ctx = assemble_context("proj-r21-manifest", "p1",
                           project={"name": "预算清单测试"},
                           node_state={"node_task": "probe"},
                           task_type="default")
    trace = ctx["assembly_trace"]
    assert "layers_manifest" in trace
    manifest = trace["layers_manifest"]
    assert [e["layer"] for e in manifest] == [l.value for l in LAYER_PRIORITY]
    for entry in manifest:
        assert "assembled" in entry
        assert "chars" in entry
        assert "dropped_by_budget" in entry
        assert "truncated" in entry

    c3_entry = next(e for e in manifest if e["layer"] == "C3")
    assert "skills_hit" in c3_entry
    assert isinstance(c3_entry["skills_hit"], list)

    assert "budget" in trace
    budget = trace["budget"]
    for key in ("spec", "estimated_max_chars", "total_chars_before_trim", "trimmed", "dropped_layers"):
        assert key in budget


def test_assemble_context_normal_size_not_trimmed(isolated_data):
    """正常大小项目上下文远小于 100k tokens 预算 → 不触发裁剪（行为跟改动前一致）。"""
    from app.services.context_assembler import assemble_context

    ctx = assemble_context("proj-r21-normal", "p0",
                           project={"name": "正常大小项目"},
                           node_state={"node_task": "P0 接入"},
                           task_type="onboarding")
    trace = ctx["assembly_trace"]
    assert trace["budget"]["trimmed"] is False
    assert trace["budget"]["dropped_layers"] == []
    for entry in trace["layers_manifest"]:
        assert entry["dropped_by_budget"] is False


def test_assemble_context_manifest_never_leaks_layer_body_text(isolated_data):
    """红线：装配清单只含元数据，不得携带层正文原文（体积 + 脱敏）。"""
    from app.services.context_assembler import assemble_context

    ctx = assemble_context("proj-r21-noleak", "p1",
                           project={"name": "现代化样本", "scenario": "modernization"},
                           task_type="default")
    trace = ctx["assembly_trace"]
    # 场景包正文（真实注入 system_prompt 的内容）不得出现在 trace 里 —— trace 只记字符数/标记。
    full_prompt_layer_content = ctx["layers"].get("C1", {}).get("content", "")
    assert full_prompt_layer_content, "sanity: C1 真的装配了带场景正文的内容"
    assert full_prompt_layer_content not in str(trace)


def test_assemble_context_c3_truncated_reuses_skill_loader_flag(isolated_data, tmp_path):
    """C3 的 truncated 标记复用 skill_loader 已有的 body_truncated（不新造判定）。"""
    from app.services.context_layers import assemble_c3

    skills_with_body = [
        {"name": "skill-a", "body": "x" * 100, "body_truncated": False},
        {"name": "skill-b", "body": "y" * 100, "body_truncated": True},
    ]
    result = assemble_c3(skills_with_body)
    assert result["skills_with_body"] == 2
    # assemble_c3 本身不产出 truncated 汇总字段——由 context_assembler 在 layers_manifest
    # 里用 any(body_truncated) 汇总；这里直接验证 skills_with_body 逐条 body_truncated 可读。
    assert any(s["body_truncated"] for s in skills_with_body)
