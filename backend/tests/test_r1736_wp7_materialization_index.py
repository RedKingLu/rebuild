"""R17.3-6 WP-7: P0 源码物化优化与索引质量 — 真实（非 mock）回归测试.

覆盖三项：
- REC-06：git 已物化且配置/commit 未变时复用已物化 source（不重 clone）+ HEAD commit
  真实性校验 + force_refresh 强制全量重 clone + 配置变更重 clone。
- ISSUE-03：generate_source_index 的 source_type 从物化元数据真实继承（非恒 unknown）、
  key_files 通用识别 .NET/.sql（endswith 非硬编码文件名）、git_info 透出。
- FUP-1：FullStackProfiler 对仅含 .sln（无 .csproj）的 .NET 解决方案识别出 .NET 栈
  与 msbuild-sln 构建系统。

全部走真实本地 git repo / 真实文件扫描，无 mock。
"""

import json
import subprocess

import pytest

from app.services import workspace_service
from app.services.source_materializer import (
    SourceMaterializer,
    generate_source_index,
    _is_key_file,
)
from app.services.full_stack_profiler import FullStackProfiler


def _git(args, cwd=None):
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_TERMINAL_PROMPT": "0",
    }
    import os
    return subprocess.run(["git", *(["-C", str(cwd)] if cwd else []), *args],
                          capture_output=True, text=True, env={**os.environ, **env})


def _make_origin(tmp_path):
    """Create a real local git origin repo with a .NET-ish file."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(["init", "-q", "-b", "main", str(origin)])
    (origin / "App.csproj").write_text("<Project/>", encoding="utf-8")
    (origin / "Program.cs").write_text("class P{}", encoding="utf-8")
    _git(["add", "-A"], cwd=origin)
    _git(["commit", "-q", "-m", "init"], cwd=origin)
    return origin


# ── REC-06 ──────────────────────────────────────────────────────────────

def test_rec06_reuse_when_config_and_commit_unchanged(client, tmp_path):
    origin = _make_origin(tmp_path)
    cfg = {"remote_url": f"file://{origin}", "branch": "main"}
    m = SourceMaterializer()
    pid = "wp7-reuse"

    r1 = m.materialize(pid, "git", cfg)
    assert r1["materialization_status"] == "completed"
    assert r1["materialization_mode"] == "clone"
    assert r1["git_info"] and r1["git_info"]["commit"]

    src = workspace_service.workspace_path(pid) / "source"
    # sentinel inside .git — a re-clone would wipe it; reuse must keep it.
    sentinel = src / ".git" / "REUSE_SENTINEL"
    sentinel.write_text("x")

    r2 = m.materialize(pid, "git", cfg)
    assert r2["materialization_mode"] == "reuse", "配置/commit 未变时应复用，不重 clone"
    assert sentinel.exists(), "复用不应重新 clone（sentinel 应保留）"
    assert r2["git_info"]["commit"] == r1["git_info"]["commit"]


def test_rec06_force_refresh_reclones(client, tmp_path):
    origin = _make_origin(tmp_path)
    cfg = {"remote_url": f"file://{origin}", "branch": "main"}
    m = SourceMaterializer()
    pid = "wp7-force"
    m.materialize(pid, "git", cfg)
    src = workspace_service.workspace_path(pid) / "source"
    sentinel = src / ".git" / "REUSE_SENTINEL"
    sentinel.write_text("x")

    r = m.materialize(pid, "git", cfg, force_refresh=True)
    assert r["materialization_mode"] == "clone", "force_refresh 必须全量重 clone"
    assert not sentinel.exists(), "force_refresh 应重新 clone（sentinel 被清除）"


def test_rec06_config_change_reclones(client, tmp_path):
    origin = _make_origin(tmp_path)
    m = SourceMaterializer()
    pid = "wp7-cfgchange"
    m.materialize(pid, "git", {"remote_url": f"file://{origin}", "branch": "main"})
    src = workspace_service.workspace_path(pid) / "source"
    sentinel = src / ".git" / "REUSE_SENTINEL"
    sentinel.write_text("x")

    # branch changed → fingerprint differs → full re-clone
    r = m.materialize(pid, "git", {"remote_url": f"file://{origin}", "branch": "changed"})
    assert r["materialization_mode"] == "clone"
    assert not sentinel.exists()


def test_rec06_commit_integrity_failure_falls_back(client, tmp_path):
    """若 .git 缺失（无法做 commit 校验）→ 不复用，回退全量 clone。"""
    origin = _make_origin(tmp_path)
    cfg = {"remote_url": f"file://{origin}", "branch": "main"}
    m = SourceMaterializer()
    pid = "wp7-integrity"
    m.materialize(pid, "git", cfg)
    src = workspace_service.workspace_path(pid) / "source"
    # 破坏 .git → 无法校验 HEAD → 必须回退 clone，而非静默复用错误 source
    import shutil
    shutil.rmtree(src / ".git")
    r = m.materialize(pid, "git", cfg)
    assert r["materialization_mode"] == "clone"
    assert (src / ".git").exists(), "回退 clone 后 .git 应重新存在"


# ── ISSUE-03 ────────────────────────────────────────────────────────────

def test_issue03_source_type_inherited_from_meta(client, tmp_path):
    origin = _make_origin(tmp_path)
    m = SourceMaterializer()
    pid = "wp7-idx-git"
    m.materialize(pid, "git", {"remote_url": f"file://{origin}", "branch": "main"})
    idx = generate_source_index(pid)
    assert idx["source_type"] == "git", "source_type 应从物化元数据真实继承，而非 unknown"
    assert idx["git_info"] and idx["git_info"]["commit"], "git_info 应透出 commit"


def test_issue03_dotnet_key_files_generic(client, tmp_path):
    """key_files 通用识别 .NET/.sql（endswith），不依赖具体项目文件名。"""
    ws = workspace_service.workspace_path("wp7-keyfiles")
    src = ws / "source"
    src.mkdir(parents=True, exist_ok=True)
    (src / "MyApp.sln").write_text("sln", encoding="utf-8")
    (src / "packages.config").write_text("<packages/>", encoding="utf-8")
    (src / "Sub").mkdir()
    (src / "Sub" / "Lib.csproj").write_text("<Project/>", encoding="utf-8")
    (src / "schema.sql").write_text("CREATE TABLE t(id int);", encoding="utf-8")
    (src / "README.md").write_text("# hi", encoding="utf-8")

    idx = generate_source_index("wp7-keyfiles")
    kf = idx["key_files"]
    assert any(k.endswith("MyApp.sln") for k in kf)
    assert any(k.endswith("packages.config") for k in kf)
    assert any(k.endswith("Lib.csproj") for k in kf)
    assert any(k.endswith("schema.sql") for k in kf)
    assert any(k.endswith("README.md") for k in kf)


def test_issue03_is_key_file_generic_matcher():
    # generic — arbitrary basename, not hardcoded to any project
    assert _is_key_file("Whatever.sln")
    assert _is_key_file("Anything.csproj")
    assert _is_key_file("X.vbproj")
    assert _is_key_file("Y.fsproj")
    assert _is_key_file("migration.sql")
    assert _is_key_file("packages.config")
    assert _is_key_file("pom.xml")
    assert not _is_key_file("random.txt")
    assert not _is_key_file("photo.png")


def test_issue03_non_dotnet_stacks_not_regressed(client, tmp_path):
    """Java/Python/Go 关键文件识别不回退。"""
    ws = workspace_service.workspace_path("wp7-multi")
    src = ws / "source"
    src.mkdir(parents=True, exist_ok=True)
    for name in ("pom.xml", "requirements.txt", "go.mod", "package.json"):
        (src / name).write_text("x", encoding="utf-8")
    idx = generate_source_index("wp7-multi")
    for name in ("pom.xml", "requirements.txt", "go.mod", "package.json"):
        assert any(k.endswith(name) for k in idx["key_files"]), f"{name} 关键文件识别回退"


# ── FUP-1（R17.5 P1 重构：框架/构建系统【识别】已下放 LLM；采集层只列 .NET 构建文件候选） ──
# 架构改动说明（非"真 bug"）：R17.5 P1 把 FullStackProfiler 从"确定性识别主线"重构为"采集工具"
# （AGENTS §2.3 / 禁止项 25/26）。原 FUP-1 断言 profile() 写 tech_stack.json 的 .NET/msbuild-sln
# 【识别结论】——该识别现由 P1 LLM(ProfilingService) 对采集事实推理产出（见 test_wp2_p1_agent_workflow）。
# 本处改为断言【采集层】大小写不敏感地把 .sln/.csproj 列为构建文件候选喂给 LLM（HC-P1-06 已删）。

def test_fup1_dotnet_sln_only_collected_as_build_candidate(client, tmp_path):
    """仅含 .sln（无 .csproj）的 .NET 解决方案：采集层应把 .sln 列为构建文件候选（识别交 LLM）。"""
    ws = workspace_service.workspace_path("wp7-fup1")
    src = ws / "source"
    src.mkdir(parents=True, exist_ok=True)
    (src / "Legacy.sln").write_text("Microsoft Visual Studio Solution File", encoding="utf-8")
    (src / "packages.config").write_text("<packages/>", encoding="utf-8")
    (src / "Default.aspx.cs").write_text("class D{}", encoding="utf-8")

    prof = FullStackProfiler()
    facts = prof.collect_facts("wp7-fup1")
    build_paths = [c["path"] for c in facts["build_file_candidates"]]
    assert "Legacy.sln" in build_paths, f"采集应列 .sln 构建文件候选，got={build_paths}"
    # packages.config 作为依赖清单候选采集（大小写不敏感、任意栈，HC-P1-02 已删）
    dep_paths = [d["path"] for d in facts["dependency_manifests"]]
    assert "packages.config" in dep_paths, f"采集应列 packages.config 依赖候选，got={dep_paths}"


def test_fup1_csproj_collected_as_build_candidate(client, tmp_path):
    ws = workspace_service.workspace_path("wp7-fup1b")
    src = ws / "source"
    src.mkdir(parents=True, exist_ok=True)
    (src / "Api.csproj").write_text("<Project/>", encoding="utf-8")
    prof = FullStackProfiler()
    facts = prof.collect_facts("wp7-fup1b")
    build_paths = [c["path"] for c in facts["build_file_candidates"]]
    assert "Api.csproj" in build_paths, f"采集应列 .csproj 构建文件候选，got={build_paths}"
    # .csproj 亦作为依赖清单候选（NuGet PackageReference），交 LLM 解析依赖
    dep_paths = [d["path"] for d in facts["dependency_manifests"]]
    assert "Api.csproj" in dep_paths
