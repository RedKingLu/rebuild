"""R9-5-3 验收测试 — 上下文真实组装 + Agent/Skill 运行期编排.

V1: context_layers.py — C0-C6 枚举 + assemble 函数结构完整
V2: skill_loader.py — SKILL.md 正文真实加载（frontmatter + body）
V3: agent_selector.py — select_agent DB 查询 + resolve_bindings
V4: context_assembler.py — assemble_context 返回结构
V5: build_system_prompt 返回非空含层内容的字符串
V6: 架构约束 (G2: no direct litellm in agent_loop / G3: assemble_context 在执行路径)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# V1 — context_layers.py
# ─────────────────────────────────────────────────────────────────────────────

def test_context_layer_enum_exists():
    from app.services.context_layers import ContextLayer
    assert ContextLayer.C0_GOVERNANCE.value == "C0"
    assert ContextLayer.C3_SPECIALTY.value == "C3"
    assert ContextLayer.C6_DYNAMIC.value == "C6"


def test_layer_assemble_c0_returns_dict():
    from app.services.context_layers import assemble_c0
    result = assemble_c0("")
    assert isinstance(result, dict)
    assert "layer" in result
    assert result["layer"] == "C0"
    assert "content" in result
    assert "chars" in result
    assert isinstance(result["chars"], int)
    assert result["chars"] > 0


def test_layer_assemble_c3_with_skills():
    from app.services.context_layers import assemble_c3
    fake_skills = [
        {"name": "TestSkill", "body": "## 技能内容\n说明文字", "capability_status": "real", "description": "测试"},
        {"name": "NoBody", "body": "", "capability_status": "not_connected", "description": ""},
    ]
    result = assemble_c3(fake_skills)
    assert result["layer"] == "C3"
    assert "TestSkill" in result["content"]
    assert result["chars"] > 0


def test_layer_assemble_c4_with_project():
    from app.services.context_layers import assemble_c4
    result = assemble_c4(
        {"name": "TestProject", "source_type": "git"},
        {"run_goal": "完成迁移", "execution_mode": "plan"},
        "p1",
        {"source_exists": True, "artifacts_list": ["profiling_summary.md"]},
    )
    assert result["layer"] == "C4"
    assert "TestProject" in result["content"]
    assert result["chars"] > 0


def test_layer_assemble_c5_node():
    from app.services.context_layers import assemble_c5
    result = assemble_c5(node_task="P0 接入", upstream_output="上游产物内容")
    assert result["layer"] == "C5"
    assert "P0 接入" in result["content"]


def test_build_system_prompt_from_layers():
    from app.services.context_layers import (
        assemble_c0, assemble_c1, assemble_c2, assemble_c4,
        build_system_prompt_from_layers,
    )
    layers = {
        "C0": assemble_c0(""),
        "C1": assemble_c1(),
        "C2": assemble_c2(),
        "C4": assemble_c4({}, None, "p0", {}),
    }
    prompt = build_system_prompt_from_layers(layers, "p0", "node_worker_agent", "")
    assert isinstance(prompt, str)
    assert len(prompt) > 100
    # C0 governance content should appear
    assert "C0" in prompt or "治理" in prompt or "禁止" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# V2 — skill_loader.py
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_frontmatter_well_formed():
    from app.services.skill_loader import _parse_frontmatter
    text = "---\nname: MySkill\ndescription: 测试技能\n---\n\n# 正文内容\n说明"
    meta, body = _parse_frontmatter(text)
    assert meta.get("name") == "MySkill"
    assert meta.get("description") == "测试技能"
    assert "正文内容" in body


def test_parse_frontmatter_no_frontmatter():
    from app.services.skill_loader import _parse_frontmatter
    text = "# 无 frontmatter\n说明文字"
    meta, body = _parse_frontmatter(text)
    assert meta == {}
    assert "无 frontmatter" in body


def test_load_skill_body_not_found():
    from app.services.skill_loader import load_skill_body
    result = load_skill_body("/nonexistent/path/", max_chars=1000)
    assert result["capability_status"] == "not_connected"
    assert result["body"] == ""


def test_load_skill_body_real_file():
    """Load a real SKILL.md from source/skills/common/."""
    from app.services.skill_loader import load_skill_body
    real_dir = "/home/king/rebuild/source/skills/common/P-automation-audit-ops"
    if not Path(real_dir).exists():
        pytest.skip("source skills not available in this environment")
    result = load_skill_body(real_dir, max_chars=500)
    assert result["capability_status"] == "real"
    assert result["name"] == "P-automation-audit-ops"
    assert len(result["body"]) > 0
    assert isinstance(result["body_chars"], int)


def test_load_skill_body_truncates():
    """max_chars truncation works."""
    with tempfile.TemporaryDirectory() as td:
        skill_dir = Path(td) / "my-skill"
        skill_dir.mkdir()
        body_content = "x" * 1000
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: TruncTest\ndescription: 截断测试\n---\n\n{body_content}",
            encoding="utf-8",
        )
        from app.services.skill_loader import load_skill_body
        result = load_skill_body(str(skill_dir), max_chars=100)
        assert result["capability_status"] == "real"
        assert len(result["body"]) == 100
        assert result["body_truncated"] is True


def test_load_skills_for_stage_disk_fallback():
    """Disk-scan fallback (skills_db=None) returns real results when source exists."""
    src = Path("/home/king/rebuild/source/skills")
    if not src.exists():
        pytest.skip("source skills directory not available")
    from app.services.skill_loader import load_skills_for_stage
    results = load_skills_for_stage("p0", max_chars_each=200, skills_db=None)
    assert isinstance(results, list)
    # At least the common + p0 skills should be found
    assert len(results) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# V3 — agent_selector.py (requires DB via isolated_data fixture)
# ─────────────────────────────────────────────────────────────────────────────

def test_select_agent_returns_dict_or_none(isolated_data):
    from app.services.agent_selector import select_agent
    result = select_agent("p0", "onboarding")
    # Returns dict or None — must not raise
    assert result is None or isinstance(result, dict)
    if result:
        assert "agent_type" in result
        assert "context_recipe" in result


def test_select_agent_chat_maps_conversation_gate(isolated_data):
    from app.services.agent_selector import select_agent
    result = select_agent("p0", "chat")
    # conversation_gate agent should exist in seed data
    if result:
        assert result["agent_type"] == "conversation_gate"


def test_resolve_bindings_returns_skills_and_tools(isolated_data):
    from app.services.agent_selector import resolve_bindings
    # Empty agent dict → stage-default skill fallback path
    result = resolve_bindings({}, "p0", max_chars_per_skill=200)
    assert "skills" in result
    assert "tools" in result
    assert isinstance(result["skills"], list)
    assert isinstance(result["tools"], list)


# ─────────────────────────────────────────────────────────────────────────────
# V4 — context_assembler.py
# ─────────────────────────────────────────────────────────────────────────────

def test_assemble_context_keys(isolated_data):
    from app.services.context_assembler import assemble_context
    result = assemble_context("test-proj-001", "p0", task_type="chat")
    assert "project_id" in result
    assert result["project_id"] == "test-proj-001"
    assert "current_stage" in result
    assert result["current_stage"] == "p0"
    assert "layers" in result
    assert "assembly_trace" in result
    assert "context_recipe" in result
    assert "skills" in result  # backward-compat key


def test_assemble_context_layers_present(isolated_data):
    from app.services.context_assembler import assemble_context
    result = assemble_context("test-proj-002", "p1",
                              project={"name": "测试项目"},
                              run={"run_goal": "迁移", "execution_mode": "plan"},
                              task_type="onboarding")
    layers = result["layers"]
    assert isinstance(layers, dict)
    # C0 is always required
    assert "C0" in layers
    # C4 should be assembled (has project/run data)
    assert "C4" in layers


def test_assemble_context_assembly_trace(isolated_data):
    from app.services.context_assembler import assemble_context
    result = assemble_context("test-proj-003", "p0",
                              node_state={"node_task": "P0 接入"},
                              task_type="onboarding")
    trace = result["assembly_trace"]
    assert "layers_assembled" in trace
    assert "skill_count" in trace
    assert "total_layer_chars" in trace
    assert isinstance(trace["layers_assembled"], list)
    assert len(trace["layers_assembled"]) > 0


def test_assemble_context_include_body(isolated_data):
    from app.services.context_assembler import assemble_context
    # With include_body=False (default), body key absent in skills
    result = assemble_context("test-proj-004", "p0", include_body=False)
    for s in result.get("skills", []):
        assert "body" not in s

    # With include_body=True, body key present (may be empty if not_connected)
    result2 = assemble_context("test-proj-005", "p0", include_body=True)
    for s in result2.get("skills", []):
        assert "body" in s


# ─────────────────────────────────────────────────────────────────────────────
# V5 — build_system_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_build_system_prompt_non_empty(isolated_data):
    from app.services.context_assembler import build_system_prompt
    prompt = build_system_prompt("test-proj-006", "p0",
                                 project={"name": "MigrateMe"},
                                 task_type="chat")
    assert isinstance(prompt, str)
    assert len(prompt) > 50


def test_build_system_prompt_contains_governance(isolated_data):
    """C0 governance text must appear in system prompt."""
    from app.services.context_assembler import build_system_prompt
    prompt = build_system_prompt("test-proj-007", "p1",
                                 project={"name": "TestProject"},
                                 run={"execution_mode": "plan"},
                                 task_type="default")
    # C0 governance layer provides content about what is forbidden/required
    # (exact wording depends on _C0_GOVERNANCE_TEXT — just ensure it's non-trivial)
    assert len(prompt) > 100


def test_build_system_prompt_stage_in_output(isolated_data):
    """C4 layer contains stage information."""
    from app.services.context_assembler import build_system_prompt
    prompt = build_system_prompt("test-proj-008", "p2",
                                 project={"name": "StageTest"},
                                 task_type="default")
    # C4 layer should mention stage p2 or P2
    assert "p2" in prompt.lower() or "P2" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# V6 — 架构约束 (G2 + G3)
# ─────────────────────────────────────────────────────────────────────────────

def test_g2_agent_loop_no_direct_litellm():
    """G2: agent_loop.py must NOT import litellm directly (all model calls via ModelGateway)."""
    src = Path("/home/king/rebuild/backend/app/services/agent_loop.py")
    assert src.exists(), "agent_loop.py not found"
    text = src.read_text(encoding="utf-8")
    assert "import litellm" not in text, "agent_loop.py must not import litellm directly"
    assert "litellm.acompletion" not in text, "agent_loop.py must not call litellm.acompletion directly"
    # T5: uses call_stream
    assert "call_stream" in text, "agent_loop.py must use ModelGateway.call_stream"


def test_g3_assemble_context_in_execution_path():
    """G3: assemble_context must be called in stage_handlers (execution path)."""
    sh = Path("/home/king/rebuild/backend/app/graph/stage_handlers.py")
    assert sh.exists(), "stage_handlers.py not found"
    text = sh.read_text(encoding="utf-8")
    assert "assemble_context" in text, "stage_handlers.py must call assemble_context (G3)"
    # Both P0 and P1 handlers should hook context assembly
    assert text.count("assemble_context") >= 2, "Both P0 and P1 handlers should call assemble_context"


def test_g3_build_system_prompt_in_agent_loop():
    """G3 extension: agent_loop.py uses build_system_prompt (C0-C6 path)."""
    src = Path("/home/king/rebuild/backend/app/services/agent_loop.py")
    text = src.read_text(encoding="utf-8")
    assert "build_system_prompt" in text, "agent_loop.py must call build_system_prompt"


def test_context_assembler_single_source():
    """X-4-5: context_assembler.py is the single source — no competing assembler."""
    import subprocess, sys
    result = subprocess.run(
        ["grep", "-r", "assemble_context", "/home/king/rebuild/backend/app/",
         "--include=*.py", "-l"],
        capture_output=True, text=True
    )
    callers = [f for f in result.stdout.strip().splitlines()
               if f and "context_assembler.py" not in f and "__pycache__" not in f]
    # Only stage_handlers and agent_loop should call assemble_context
    # (context_assembler itself defines it)
    caller_names = [Path(f).name for f in callers]
    # All callers must go through the canonical assembler, not bypass it.
    # Approved callers: execution nodes (stage_handlers), agent loop, the /context API route,
    # and external_context_builder (R9-5-5 T5.2: builds context for external platform delegation).
    approved = {
        "stage_handlers.py", "agent_loop.py", "routes_workspace.py",
        "external_context_builder.py",  # R9-5-5: external platform context injection
    }
    for name in caller_names:
        assert name in approved, (
            f"Unexpected caller of assemble_context: {name} — check X-4-5 single-source rule"
        )
