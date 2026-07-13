"""R17.2 — apply_patch real unified-diff engine (REC→R17.2).

_execute_apply_patch previously wrote the draft's "diff" field verbatim as the whole
file content. When that field is an actual unified diff, writing it raw corrupts the
target. These tests exercise the stdlib diff engine end to end (generate_patch draft →
apply_patch), covering:
  1. unified diff applied hunk-by-hunk onto an existing output_code/ base
  2. plain new content → full-content write (backward compatible)
  3. bad hunk context → rejected, target file NOT corrupted (D-097)

Uses the autouse isolated_data fixture (temp DB + workspace) from conftest; no shared
state is mutated, so no explicit teardown is required.
"""

from app.services import tool_registry
from app.services.tool_registry import (
    apply_unified_diff,
    _looks_like_unified_diff,
)
from app.services.workspace_service import init_workspace, workspace_path


_PID = "proj-apply-patch-diff"


def _init():
    init_workspace(_PID)
    return workspace_path(_PID)


def test_looks_like_unified_diff_detection():
    assert _looks_like_unified_diff("@@ -1,2 +1,2 @@\n a\n-b\n+c\n")
    assert _looks_like_unified_diff("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
    assert _looks_like_unified_diff("diff --git a/x b/x\n")
    # Plain whole-file content is NOT a diff.
    assert not _looks_like_unified_diff("hello world\nno diff here\n")
    assert not _looks_like_unified_diff("")


def test_apply_unified_diff_pure_function():
    base = 'def greet(name):\n    print("hi")\n    return name\n'
    diff = "\n".join([
        "@@ -1,3 +1,3 @@",
        " def greet(name):",
        '-    print("hi")',
        '+    print("hello")',
        "     return name",
    ]) + "\n"
    out = apply_unified_diff(base, diff)
    assert out == 'def greet(name):\n    print("hello")\n    return name\n'


async def test_apply_patch_unified_diff_merges_onto_base():
    ws = _init()
    target = ws / "output_code" / "greet.py"
    base = 'def greet(name):\n    print("hi")\n    return name\n'
    target.write_text(base, encoding="utf-8")

    diff = "\n".join([
        "@@ -1,3 +1,3 @@",
        " def greet(name):",
        '-    print("hi")',
        '+    print("hello")',
        "     return name",
    ]) + "\n"

    drafted = await tool_registry._execute_generate_patch(
        "generate_patch",
        {"target_path": "output_code/greet.py", "diff": diff},
        _PID, None,
    )
    assert drafted["status"] == "patch_drafted"
    patch_ref = drafted["patch_ref"]

    applied = await tool_registry._execute_apply_patch(
        "apply_patch_with_confirm",
        {"patch_ref": patch_ref, "target_path": "output_code/greet.py"},
        _PID, None,
    )
    assert applied["status"] == "patch_applied"
    assert applied["apply_mode"] == "unified_diff"
    # The file is the MERGED result — not the raw diff text.
    expected = 'def greet(name):\n    print("hello")\n    return name\n'
    assert target.read_text("utf-8") == expected
    assert "@@" not in target.read_text("utf-8")


async def test_apply_patch_full_content_fallback():
    ws = _init()
    target = ws / "output_code" / "new_module.py"
    new_content = "# a brand new file\nprint('created')\n"

    drafted = await tool_registry._execute_generate_patch(
        "generate_patch",
        {"target_path": "output_code/new_module.py", "diff": new_content},
        _PID, None,
    )
    patch_ref = drafted["patch_ref"]

    applied = await tool_registry._execute_apply_patch(
        "apply_patch_with_confirm",
        {"patch_ref": patch_ref, "target_path": "output_code/new_module.py"},
        _PID, None,
    )
    assert applied["status"] == "patch_applied"
    assert applied["apply_mode"] == "full_content"
    assert target.read_text("utf-8") == new_content


async def test_apply_patch_bad_context_rejected_no_corruption():
    ws = _init()
    target = ws / "output_code" / "greet.py"
    original = 'def greet(name):\n    print("hi")\n    return name\n'
    target.write_text(original, encoding="utf-8")

    # A delete line whose context does NOT match the base.
    bad_diff = "\n".join([
        "@@ -1,3 +1,3 @@",
        " def greet(name):",
        '-    print("THIS DOES NOT MATCH")',
        '+    print("x")',
        "     return name",
    ]) + "\n"

    drafted = await tool_registry._execute_generate_patch(
        "generate_patch",
        {"target_path": "output_code/greet.py", "diff": bad_diff},
        _PID, None,
    )
    patch_ref = drafted["patch_ref"]

    applied = await tool_registry._execute_apply_patch(
        "apply_patch_with_confirm",
        {"patch_ref": patch_ref, "target_path": "output_code/greet.py"},
        _PID, None,
    )
    assert applied["status"] == "rejected"
    assert "error" in applied
    # Critical (D-097): the target file was NOT touched — still the original content.
    assert target.read_text("utf-8") == original


async def test_apply_patch_source_target_rejected():
    """source/ remains read-only for the apply target (D-099①)."""
    _init()
    drafted = await tool_registry._execute_generate_patch(
        "generate_patch",
        {"target_path": "output_code/x.py", "diff": "whatever\n"},
        _PID, None,
    )
    patch_ref = drafted["patch_ref"]
    applied = await tool_registry._execute_apply_patch(
        "apply_patch_with_confirm",
        {"patch_ref": patch_ref, "target_path": "source/x.py"},
        _PID, None,
    )
    assert applied["status"] == "rejected"
