"""R17.4-2 P0 确定性富化修复单测（WP-A/B/C/D）。

覆盖裁决 Q-R17.4-1-1「确定性→P0」补齐的确定性字段：
  WP-A source_index 富化（key_files 大小写不敏感 / entry_points /
        repository_metadata 全字段[remote 脱敏] / database_files 入口识别 / code_scale）
  WP-B intake_report 富化（repository_metadata / source_environment_clues[连接串不回显] /
        database_entry / availability_classification / migration_intent[缺则诚实标] /
        p1_intake_tasks / missing_information）
  WP-C work_agent 栈主语言加权（.sln→框架栈优先，不因 .js 多标 JS 主）+ PHP 误报修复
  WP-D branch 不一致诚实发声 / trace run_id / 脱敏红线

真实读取/测量，无 mock；样本实例值来自真实构造的源树，不硬编码进被测代码。
"""

import subprocess

import pytest

from app.services import workspace_service
from app.services.source_materializer import (
    _is_key_file, _sanitize_git_remote, _analyze_sql_file,
    _capture_repository_metadata, generate_source_index, SourceMaterializer,
    _detect_sql_encoding,
)
import app.graph.stage_handlers as sh
from app.services.work_agent import WorkAgent, _framework_primary_language


PID = "proj-r1742-test"


def _src(pid: str = PID):
    workspace_service.init_workspace(pid)
    s = workspace_service.workspace_path(pid) / "source"
    s.mkdir(parents=True, exist_ok=True)
    return s


def _write(root, rel, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


# ── WP-A ─────────────────────────────────────────────────────────────────────

def test_r174_2_keyfile_case_insensitive():
    # .NET convention capitalises Web.config / Global.asax — must still match.
    assert _is_key_file("Web.config")
    assert _is_key_file("web.config")
    assert _is_key_file("Web.Debug.config")   # transform overlay via .config suffix
    assert _is_key_file("Global.asax")
    assert _is_key_file("MicroOA.SLN")         # suffix case-insensitive
    assert _is_key_file("packages.config")
    assert not _is_key_file("index.html")


def test_r174_2_sql_encoding_and_analysis(tmp_path):
    # UTF-16LE with BOM, SQL Server (T-SQL) dialect markers, INSERT without INTO.
    sql = ("SET ANSI_NULLS ON\nCREATE TABLE [dbo].[T1] (id INT IDENTITY(1,1))\n"
           "GO\nINSERT [dbo].[T1] VALUES (1)\nINSERT [dbo].[T1] VALUES (2)\n"
           "CREATE TABLE [dbo].[T2] (name NVARCHAR(50))\n")
    raw = sql.encode("utf-16")  # produces a BOM
    assert raw[:2] == b"\xff\xfe"
    label, dec = _detect_sql_encoding(raw)
    assert "UTF-16LE" in label
    f = tmp_path / "db.sql"
    f.write_bytes(raw)
    info = _analyze_sql_file(f, len(raw))
    assert "UTF-16LE" in info["encoding"]
    assert info["dialect"] == "SQL Server (T-SQL)"
    assert info["create_table_count"] == 2
    assert info["insert_count"] == 2            # INSERT counted without mandatory INTO
    assert info["deep_schema_profiling"].startswith("NOT_DONE_IN_P0")


def test_r174_2_source_index_enrichment():
    root = _src()
    _write(root, "Web.config", '<configuration><system.web>'
           '<compilation targetFramework="4.8"/></system.web></configuration>')
    _write(root, "Global.asax", "<%@ Application %>")
    _write(root, "Default.aspx", "<%@ Page %>")
    _write(root, "App_Code/Foo.cs", "class Foo {}")
    _write(root, "Scripts/app.js", "console.log(1)")
    _write(root, "Resource/DB/init.sql",
           "CREATE TABLE [dbo].[X] (id INT IDENTITY(1,1))\nGO\n".encode("utf-16"))

    idx = generate_source_index(PID, source_type="local_dir")
    bases = {k.replace("\\", "/").split("/")[-1] for k in idx["key_files"]}
    assert {"Web.config", "Global.asax"} <= bases       # case-insensitive hit
    ep_bases = {e.split("/")[-1] for e in idx["entry_points"]}
    assert {"Default.aspx", "Global.asax"} <= ep_bases
    assert idx["database_files"], "database_files must be identified"
    db0 = idx["database_files"][0]
    assert "UTF-16" in db0["encoding"]
    assert db0["dialect"] == "SQL Server (T-SQL)"
    assert db0["create_table_count"] == 1
    assert ".cs" in idx["code_scale"]["extension_counts"]
    assert idx["code_scale"]["total_code_files"] == idx["file_count"]


def test_r174_2_repository_metadata_full_and_token_stripped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**subprocess.os.environ}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    # a remote URL carrying an injected token — must NEVER be surfaced verbatim.
    subprocess.run(["git", "remote", "add", "origin",
                    "https://x-access-token:gho_SECRETTOKEN@github.com/o/r.git"],
                   cwd=repo, check=True)

    meta = _capture_repository_metadata(repo)
    assert meta is not None
    assert meta["commit"] and meta["short_commit"] == meta["commit"][:8]
    assert meta["working_tree_status"] == "clean"
    assert meta["shallow"] is False
    assert "gho_SECRETTOKEN" not in (meta["remote"] or "")   # 脱敏红线
    assert meta["remote"] == "https://github.com/o/r.git"


def test_r174_2_sanitize_git_remote():
    assert _sanitize_git_remote(
        "https://x-access-token:gho_ABC@github.com/o/r.git") == "https://github.com/o/r.git"
    assert _sanitize_git_remote(
        "https://user:pass@gitlab.com/a/b.git") == "https://gitlab.com/a/b.git"
    # scp-like (no secret) unchanged
    assert _sanitize_git_remote("git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert _sanitize_git_remote(None) is None


# ── WP-D branch mismatch ──────────────────────────────────────────────────────

def test_r174_2_branch_mismatch_voiced():
    result = {"warnings": [], "evidence_gaps": [], "git_info": {"branch": "master"}}
    SourceMaterializer._voice_branch_mismatch("main", result, None)
    gaps = [g for g in result["evidence_gaps"] if g.get("type") == "branch_mismatch"]
    assert gaps and gaps[0]["requested_branch"] == "main" and gaps[0]["actual_branch"] == "master"
    assert result["warnings"], "mismatch must be voiced, not silent"


def test_r174_2_branch_match_no_gap():
    result = {"warnings": [], "evidence_gaps": [], "git_info": {"branch": "main"}}
    SourceMaterializer._voice_branch_mismatch("main", result, None)
    assert not [g for g in result["evidence_gaps"] if g.get("type") == "branch_mismatch"]


# ── WP-B intake enrichment ────────────────────────────────────────────────────

def _build_dotnet_source(pid):
    root = _src(pid)
    _write(root, "Web.config",
           '<configuration><system.web>'
           '<authentication mode="None"/>'
           '<sessionState mode="InProc"/>'
           '<compilation targetFramework="4.8"/></system.web>'
           '<connectionStrings><add name="db" providerName="System.Data.SqlClient" '
           'connectionString="Server=.;Password=SUPERSECRET"/></connectionStrings>'
           '</configuration>')
    _write(root, "App.sln",
           'Project GlobalSection\nDebug.AspNetCompiler\n'
           'TargetFrameworkMoniker = ".NETFramework,Version%3Dv4.8"\n')
    _write(root, "packages.config",
           '<packages><package id="A" targetFramework="net48"/>'
           '<package id="B" targetFramework="net48"/></packages>')
    _write(root, "README.md", "This is a .NET ASP.NET WebForms app using SQL Server on IIS.")
    _write(root, "App_Code/Foo.cs", "class Foo {}")
    _write(root, "Resource/DB/init.sql",
           "CREATE TABLE [dbo].[X] (id INT IDENTITY(1,1))\nGO\n".encode("utf-16"))
    return root


def test_r174_2_intake_enrichment_fields():
    pid = "proj-r1742-intake"
    _build_dotnet_source(pid)
    idx = generate_source_index(pid, source_type="local_dir")
    enr = sh._build_intake_enrichment(
        pid, idx, {"materialization_status": "completed", "file_count": idx["file_count"]},
        src_type="local_dir", source_config={})

    # availability B (source available, key files present) with reasoning chain
    assert enr["availability_classification"]["class"] == "B"
    assert enr["availability_classification"]["reasoning"]

    # environment clues — real regex extraction; connection string VALUE not echoed
    wc = enr["source_environment_clues"]["dotnet_web_config"]
    assert wc["target_framework"] == "4.8"
    assert wc["authentication_mode"] == "None"
    assert wc["session_state_mode"] == "InProc"
    assert wc["has_connection_strings"] is True
    clues_blob = str(enr["source_environment_clues"])
    assert "SUPERSECRET" not in clues_blob            # 脱敏红线：连接串值不回显
    assert enr["source_environment_clues"]["solution"]["is_website_project"] is True
    assert enr["source_environment_clues"]["packages"]["package_count"] == 2
    assert ".NET" in enr["source_environment_clues"]["readme_keywords"]
    assert enr["source_environment_clues"]["deep_environment_profiling"].startswith("NOT_DONE_IN_P0")

    # database entry
    assert enr["database_entry"]["present"] is True
    assert enr["database_entry"]["primary"]["dialect"] == "SQL Server (T-SQL)"

    # migration_intent honestly not_provided (no config/description)
    assert enr["migration_intent"]["text"] is None
    assert enr["migration_intent"]["note"] == "not_provided_at_p0"
    assert enr["migration_intent"]["decision_status"] == "pending_p2_assessment"

    # p1 intake tasks — ten categories
    assert len(enr["p1_intake_tasks"]) == 10
    cats = {t["category"] for t in enr["p1_intake_tasks"]}
    assert "migration_sensitive_points" in cats and "db_objects" in cats

    # missing information — no LICENSE + intent absent
    assert any("LICENSE" in m for m in enr["missing_information"])
    assert enr["questions_for_user"]
    assert enr["repository_metadata"] == idx.get("repository_metadata")


def test_r174_2_migration_intent_from_config():
    pid = "proj-r1742-intent"
    _src(pid)
    intent = sh._build_migration_intent(pid, {"migration_intent": "迁移到信创环境"})
    assert intent["text"] == "迁移到信创环境"
    assert intent["source"] == "user_intent"
    assert "note" not in intent


def test_r174_2_availability_empty_source_is_C():
    cls = sh._build_availability_classification({}, 0, {"materialization_status": "empty"})
    assert cls["class"] == "C"


# ── WP-C stack weighting + PHP fix ────────────────────────────────────────────

def test_r174_2_framework_primary_language():
    assert _framework_primary_language(["App.sln"], {"C#": 10, "JavaScript": 200}) == "C#"
    assert _framework_primary_language(["App.vbproj"], {"Visual Basic": 5, "C#": 0}) == "Visual Basic"
    assert _framework_primary_language(["pom.xml"], {"Java": 20}) == "Java"
    assert _framework_primary_language(["go.mod"], {"Go": 3}) == "Go"
    assert _framework_primary_language(["package.json"], {"JavaScript": 5}) is None


def test_r174_2_scan_facts_dotnet_primary_no_php():
    pid = "proj-r1742-stack"
    root = _src(pid)
    _write(root, "MicroOA.sln", "solution")
    # many vendored JS/TS static assets (must NOT dominate primary language)
    for i in range(30):
        _write(root, f"Scripts/a{i}.js", "x")
    for i in range(20):
        _write(root, f"Resource/t{i}.ts", "x")
    # real app code (.cs) — fewer files than JS but the framework language
    for i in range(8):
        _write(root, f"App_Code/C{i}.cs", "class C {}")
    # vendored third-party PHP example files (PHP 误报根因) under examples/
    _write(root, "Resource/fullcalendar/examples/php/a.php", "<?php ?>")
    _write(root, "Resource/fullcalendar/examples/php/b.php", "<?php ?>")
    # data/doc noise must not become a "language"
    _write(root, "package.json", "{}")
    _write(root, "README.md", "# doc")

    facts = WorkAgent("p0", pid, "run-x")._scan_project_facts({"source_type": "local_dir"})
    assert facts["framework_primary"] == "C#"
    assert facts["primary_language"] == "C#"          # not JavaScript despite 30 .js
    assert "PHP" not in facts["detected_stack"]        # vendored examples excluded
    assert "JSON" not in facts["detected_stack"]       # data ext not a language
    assert "Markdown" not in facts["detected_stack"]
