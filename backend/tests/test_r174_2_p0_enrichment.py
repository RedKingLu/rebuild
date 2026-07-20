"""R17.5 P0 目标驱动 Agent 重构单测（承接原 R17.4-2 P0 富化测试）。

架构改动说明（R17.5，非"真 bug"）：P0 从"确定性识别富化"重构为"目标驱动 Node Worker
Agent 循环"——确定性【只做采集】（clone/列文件/读/计数/编码/脱敏），识别/技术栈/环境解读/
可用性研判/P1 任务规划/DB 入口/入口点判定【一律 LLM】（IntakeService 经 ModelGateway）。
因此原 R17.4-2 的【确定性识别】单测（_build_intake_enrichment / _extract_environment_clues /
_build_availability_classification / _build_p1_intake_tasks / _framework_primary_language 等）
已随被测函数删除而失效——本文件保留【采集层】单测（编码/计数/key_file/git 元数据/脱敏/分支
一致性），并新增【采集事实包】与【IntakeService 无 Key 诚实 blocked / 有 Key 识别】单测。

真实读取/测量，无 mock 生产路径（IntakeService gateway 注入仅为可测性，同 AssessmentService 范式）。
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
from app.services.work_agent import WorkAgent
from app.services.intake_service import IntakeService, redact_config_text


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


# ── 采集层：key file / 编码 / 计数（保留，测采集测量） ──────────────────────────

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


def test_r174_2_source_index_collection():
    """采集：source_index 列文件/计数/key_files（大小写不敏感）/database_files（编码/方言）/
    code_scale。R17.5：entry_points 不再由采集预判（识别交 LLM）——应为空 + note。"""
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
    # R17.5 WP-2/WP-3: 采集不预判入口点（识别交 P0 LLM）。
    assert idx["entry_points"] == []
    assert idx.get("entry_points_note")
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


# ── WP-D branch mismatch（采集侧诚实发声） ─────────────────────────────────────

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


# ── R17.5 采集事实包 + 脱敏 ────────────────────────────────────────────────────

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


def test_r175_intake_facts_collection_only_and_redacted():
    """采集事实包只含事实，不含识别结论；连接串/密钥值全程脱敏（D-032）。"""
    pid = "proj-r175-facts"
    _build_dotnet_source(pid)
    idx = generate_source_index(pid, source_type="local_dir")
    facts = sh._build_intake_facts(
        pid, idx, {"materialization_status": "completed", "file_count": idx["file_count"]},
        src_type="local_dir", source_config={})

    # 采集字段存在（事实），且【无识别结论字段】（availability/primary_language 等交 LLM）。
    assert facts["file_count"] == idx["file_count"]
    assert facts["key_files_candidates"], "采集给关键文件候选（交 LLM 判）"
    assert "availability_classification" not in facts
    assert "source_environment_clues" not in facts
    assert "p1_intake_tasks" not in facts
    assert "primary_language" not in facts

    # 关键文件原文已采集且脱敏——连接串密码值绝不出现。
    blob = str(facts["key_file_contents_redacted"])
    assert "SUPERSECRET" not in blob, "脱敏红线：连接串值不回显"
    assert facts["migration_intent_present"] is False   # 无 config/description 意图


def test_r175_redact_config_text():
    txt = ('<add connectionString="Server=.;Password=SUPERSECRET"/>'
           '\napi_key = ABCD1234EFGH5678')
    out = redact_config_text(txt)
    assert "SUPERSECRET" not in out
    assert "ABCD1234EFGH5678" not in out
    assert "[REDACTED]" in out


def test_r175_migration_intent_registration_from_config():
    # 采集登记用户迁移意图原文（归纳交 LLM；此处仅测采集登记）。
    pid = "proj-r175-intent"
    _src(pid)
    intent = sh._build_migration_intent(pid, {"migration_intent": "迁移到信创环境"})
    assert intent["text"] == "迁移到信创环境"
    assert intent["source"] == "user_intent"
    assert "note" not in intent


# ── R17.5 IntakeService：无 Key 诚实 blocked / 有 Key（注入 gateway）识别 ─────────

class _BlockedGateway:
    """模拟无可用模型（无 Key）：readiness available=False。"""
    def stage_model_readiness(self, **kw):
        return {"available": False, "capability_ok": False,
                "reason": "无任一已配置且具备有效凭据的模型可用",
                "attempted_chain": [{"provider": "x", "outcome": "credential_missing"}],
                "user_actions": [{"action": "configure_key"}]}


class _KeyPresentGateway:
    """模拟 Key-present LLM：readiness available=True，call 返回结构化识别 JSON。"""
    def __init__(self, content):
        self._content = content

    def stage_model_readiness(self, **kw):
        return {"available": True, "capability_ok": True, "reason": "",
                "attempted_chain": [], "user_actions": []}

    async def call(self, *, messages=None, **kw):
        return {"status": "completed", "model": "fake-model", "content": self._content}


@pytest.mark.asyncio
async def test_r175_intake_blocked_no_key():
    svc = IntakeService(gateway=_BlockedGateway())
    res = await svc.identify("pid-x", facts={"file_count": 3})
    assert res.status == "blocked"
    assert "no_model_key" in res.reason
    assert res.model_error_category == "model_unavailable"
    assert res.attempted_chain, "无 Key 须透传已尝试/候选模型链路（诚实）"


@pytest.mark.asyncio
async def test_r175_intake_completed_with_key():
    import json
    ident = {
        "primary_language": "C#",
        "detected_stack": ["C#", "JavaScript", "SQL"],
        "key_files": [{"path": "Web.config", "why_key": "配置入口"}],
        "availability_classification": {"class": "B", "label": "源码可用未验证运行",
                                        "reasoning": ["源码已物化", "无实跑证据"]},
        "database_entry": {"present": True, "dialect": "SQL Server (T-SQL)"},
        "migration_intent": {"text": None, "source": "user_intent",
                             "confidence": "unverified", "decision_status": "pending_p2_assessment"},
        "entry_points": [{"path": "Global.asax", "kind": "asp_net_app"}],
        "p1_intake_tasks": [{"category": "db_objects", "task": "识别表", "scope_ref": "Resource/DB"}],
        "missing_information": ["未发现 LICENSE"],
        "questions_for_user": ["目标数据库？"],
        "uncertainty": [{"area": "运行环境", "detail": "未验证实跑"}],
    }
    svc = IntakeService(gateway=_KeyPresentGateway(json.dumps(ident, ensure_ascii=False)))
    res = await svc.identify("pid-y", facts={"file_count": 10,
                                             "migration_target": {"os": "openEuler"}})
    assert res.status == "completed"
    assert res.identification["primary_language"] == "C#"
    assert res.identification["availability_classification"]["class"] == "B"
    # 目标运行环境（用户输入采集）透传进 identification
    assert res.identification["migration_target"]["os"] == "openEuler"
