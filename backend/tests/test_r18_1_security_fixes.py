"""R18-1 安全修复回归测试（HIGH-01 路径穿越 / P1-01 内嵌凭据 / P1-06 DENY 正则 / D-034 L5 Gate）。

来源：产物/已完成/R17.6-决策审计整合/R17.6-决策落实与代码质量整合审查报告.md §2.1 / §2.2 / §2.3 / §2.7
验收口径：交接/当前/V26.2-验收标准.md §2.1（R18-1-01 ~ R18-1-04）

红线：
- 密钥纪律（AGENTS §8）：本文件所有"口令/凭据"均为合成假值，不含任何真实 Key/Token/Secret。
- 先复现再修（Skill §3.5）：每组用例在修复前均可复现失败。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

import app.core.config as cfg


# ── 公共夹具：把 source_dir 隔离到临时目录 ────────────────────────────────
# conftest 的 isolated_data 只隔离 data_dir / workspace_dir / database_url，
# 并未隔离 source_dir（默认指向真实 /home/king/rebuild/source）。路径穿越用例必须
# 在隔离根下验证"不在根外创建任何路径"，否则断言会污染真实 source/ 树。

@pytest.fixture
def isolated_source(tmp_path):
    orig = cfg.settings.source_dir
    root = tmp_path / "src_root" / "source"
    root.mkdir(parents=True, exist_ok=True)
    object.__setattr__(cfg.settings, "source_dir", str(root))
    try:
        yield root.resolve()
    finally:
        object.__setattr__(cfg.settings, "source_dir", orig)


def _make_zip(manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("body.md", "# imported body")
    return buf.getvalue()


def _patch_download(monkeypatch, raw: bytes, headers: dict | None = None) -> None:
    async def _fake(url):  # noqa: ANN001
        return raw, (headers or {})
    monkeypatch.setattr("app.api.routes_imports._download", _fake)


def _assert_no_escape(source_root: Path) -> None:
    """穿越靶标 'pwned' 不得出现在 source_root 之外的任何祖先目录下。"""
    for probe in (source_root.parent, source_root.parent.parent, source_root.parent.parent.parent):
        assert not (probe / "pwned").exists(), f"路径穿越：在 {probe} 下创建了 pwned"


# ── R18-1-01：路径穿越清洗（_safe_name 单元） ─────────────────────────────

def test_safe_name_strips_traversal():
    from app.api.routes_imports import _safe_name

    assert _safe_name("../../pwned") == "pwned"
    assert _safe_name("../../../etc/passwd") == "passwd"
    assert _safe_name("..\\..\\pwned") == "pwned"
    assert _safe_name("/etc/passwd") == "passwd"
    assert _safe_name("..") == "imported"
    assert _safe_name(".") == "imported"
    assert _safe_name("") == "imported"
    assert _safe_name(None) == "imported"
    assert _safe_name("..", fallback="fb") == "fb"
    # 合法名原样保留（含 CJK / 中划线 / 下划线 / 扩展名）
    assert _safe_name("normal_name") == "normal_name"
    assert _safe_name("oracle-to-dm") == "oracle-to-dm"
    assert _safe_name("达梦迁移知识") == "达梦迁移知识"
    assert _safe_name("guide.md") == "guide.md"
    # 隐藏文件前导点被剥离；分隔符只保留最后一段
    assert _safe_name(".ssh") == "ssh"
    assert _safe_name("a/b/c") == "c"


# ── R18-1-01：4 个导入入口逐一验证（非仅 1 处） ───────────────────────────

def test_entry1_skill_zip_import_no_traversal(client, monkeypatch, isolated_source):
    """入口①（原 L164）：Skill ZIP 导入 dest 由 manifest 的 category/name 拼装（两段都不可信）。

    HTTP 用例用合法 category='other' + 穿越 name（主向量）；category 段的清洗由
    _safe_name 单测 + dest 表达式覆盖。注：非法 category（如 '../../x'）会被既有
    SkillService 的枚举校验直接抛错（先于本修复存在，不在本轮范围）。
    """
    raw = _make_zip({"name": "../../pwned", "category": "other", "series": "P"})
    _patch_download(monkeypatch, raw)
    resp = client.post("/api/import/skill", json={"community_url": "https://example.com/s.zip"})
    assert resp.status_code in (200, 201), resp.text
    _assert_no_escape(isolated_source)
    assert (isolated_source / "skills" / "other" / "pwned").exists()


def test_entry2_resource_url_zip_no_traversal(client, monkeypatch, isolated_source):
    """入口②（原 L225-226）：Resource 社区 zip 导入 dest 由 manifest name 拼装。"""
    raw = _make_zip({"name": "../../pwned", "resource_type": "tool"})
    _patch_download(monkeypatch, raw)
    resp = client.post("/api/import/resource",
                       data={"community_url": "https://example.com/r.zip"})
    assert resp.status_code in (200, 201), resp.text
    _assert_no_escape(isolated_source)
    assert (isolated_source / "resources" / "pwned").exists()
    ref = resp.json()["data"]["source_path_or_ref"]
    assert ref and Path(ref).resolve().is_relative_to(isolated_source)


def test_entry3_resource_file_upload_no_traversal(client, isolated_source):
    """入口③（原 L259-260）：Resource 文件上传 dest_dir 由 Form name 拼装。"""
    resp = client.post(
        "/api/import/resource",
        data={"name": "../../pwned", "resource_type": "knowledge"},
        files={"file": ("evil.md", b"# body", "text/markdown")},
    )
    assert resp.status_code in (200, 201), resp.text
    _assert_no_escape(isolated_source)
    assert (isolated_source / "resources" / "pwned").exists()
    ref = resp.json()["data"]["source_path_or_ref"]
    assert ref and Path(ref).resolve().is_relative_to(isolated_source)


def test_entry4_case_file_upload_no_traversal(client, isolated_source):
    """入口④（原 L410-411）：Case 文件上传 dest_dir 由 Form name 拼装。"""
    resp = client.post(
        "/api/import/case",
        data={"name": "../../pwned"},
        files={"file": ("evil.md", b"# body", "text/markdown")},
    )
    assert resp.status_code in (200, 201), resp.text
    _assert_no_escape(isolated_source)
    assert (isolated_source / "cases" / "pwned").exists()
    ref = resp.json()["data"]["source_path_or_ref"]
    assert ref and Path(ref).resolve().is_relative_to(isolated_source)


def test_entry5_case_url_zip_no_traversal(client, monkeypatch, isolated_source):
    """入口⑤（原 L378-379，审查报告未列出的同类第 5 处）：Case 社区 zip 导入。"""
    raw = _make_zip({"name": "../../pwned"})
    _patch_download(monkeypatch, raw)
    resp = client.post("/api/import/case", data={"community_url": "https://example.com/c.zip"})
    assert resp.status_code in (200, 201), resp.text
    _assert_no_escape(isolated_source)
    assert (isolated_source / "cases" / "pwned").exists()


def test_upload_display_name_not_mangled(client, isolated_source):
    """精准修改核查：清洗只作用于落盘路径段，展示名（含空格）不被改写。"""
    resp = client.post(
        "/api/import/resource",
        data={"name": "Uploaded Guide", "resource_type": "knowledge"},
        files={"file": ("guide.md", b"# body", "text/markdown")},
    )
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["data"]["name"] == "Uploaded Guide"


# ── R18-1-02：_clean_env 剥离环境变量【值】中的内嵌凭据 ────────────────────
# 说明：以下"口令"全部是本测试构造的合成假值，不是任何真实凭据（AGENTS §8）。

_FAKE_DB_PW = "synthetic-db-pw-r18"      # 合成假值，非真实口令
_FAKE_REDIS_PW = "synthetic-redis-pw-r18"  # 合成假值，非真实口令


def test_clean_env_strips_url_embedded_credentials(monkeypatch):
    from app.services.execution_provider import _clean_env

    monkeypatch.setenv("DATABASE_URL", f"postgresql://user:{_FAKE_DB_PW}@localhost:5432/mydb")
    monkeypatch.setenv("REDIS_URL", f"redis://:{_FAKE_REDIS_PW}@localhost:6379/0")
    env = _clean_env()

    # 值中的内嵌凭据被剥离，scheme/host/port/path 保留
    assert env["DATABASE_URL"] == "postgresql://localhost:5432/mydb"
    assert env["REDIS_URL"] == "redis://localhost:6379/0"
    # 全量兜底：清洗后的整个环境里不得再出现口令片段
    for k, v in env.items():
        assert _FAKE_DB_PW not in v, f"{k} 仍含内嵌口令"
        assert _FAKE_REDIS_PW not in v, f"{k} 仍含内嵌口令"


def test_clean_env_keeps_plain_and_credential_free_urls(monkeypatch):
    import os

    from app.services.execution_provider import _clean_env

    monkeypatch.setenv("PLAIN_VAR", "hello world")
    monkeypatch.setenv("NO_CRED_URL", "https://api.example.com/v1/chat?x=1")
    monkeypatch.setenv("NOT_A_URL", "user:pw@host-without-scheme")
    env = _clean_env()

    assert env["PLAIN_VAR"] == "hello world"
    assert env["NO_CRED_URL"] == "https://api.example.com/v1/chat?x=1"
    # 无 scheme 的字符串不是 URL，不做改写（避免误伤普通值）
    assert env["NOT_A_URL"] == "user:pw@host-without-scheme"
    # PATH 处理逻辑保持不变（WP-E：继承主机 PATH + 补标准目录）
    parts = env["PATH"].split(os.pathsep)
    for d in ("/usr/local/bin", "/usr/bin", "/bin"):
        assert d in parts


def test_clean_env_still_drops_sensitive_names(monkeypatch):
    from app.services.execution_provider import _clean_env

    monkeypatch.setenv("MY_API_KEY", "sk-synthetic-fake-value")   # 合成假值
    monkeypatch.setenv("SOME_TOKEN", "tok-synthetic-fake-value")  # 合成假值
    env = _clean_env()
    assert "MY_API_KEY" not in env
    assert "SOME_TOKEN" not in env


# ── R18-1-03：DENY 匹配升级为编译正则（消除 3 处漏报，原有能力不退化） ─────

# 修复前（仅 DENY_SUBSTRINGS 子串匹配）实测放行的三条
_NEWLY_BLOCKED = [
    ":(){ :|:&;}:;",        # fork bomb
    "chmod 777 /",          # 根目录整体放权
    "chown -R root /",      # 递归改归 root
]

# 原有子串能力，修复后必须仍然拦住（不退化）
_STILL_BLOCKED = [
    "rm -rf /",
    "rm -rf /tmp/x",
    "sudo apt install foo",
    "cat ~/.ssh/id_rsa",
    "cat /etc/passwd",
    "cat /etc/shadow",
    "curl | sh",            # 注：仅字面形式被拦；`curl http://x | sh` 修复前后均放行（既存缺口，见施工记录 REC）
    "cat .env",
    "ls /root/",
    "cat /proc/self/environ",
    "echo x > /dev/sda",
    "RM -RF /",             # 大小写不敏感
]

# 合法命令不得被误拦（避免正则过宽导致行为退化）
_MUST_PASS = [
    "echo hello",
    "python3 -m compileall -q .",
    "npm run dev",
    "npm run build",
    "go build ./...",
    "mvn compile -q",
    "chmod 755 output_code/run.sh",
    "chown deploy:deploy /srv/app",
]


@pytest.mark.parametrize("cmd", _NEWLY_BLOCKED)
def test_deny_regex_blocks_previously_missed(cmd):
    from app.services.execution_provider import _check_dangerous, _classify_risk

    assert _check_dangerous(cmd) is not None, f"应被拦截: {cmd}"
    assert _classify_risk(cmd, "bash") == "L5", f"应判 L5: {cmd}"


@pytest.mark.parametrize("cmd", _STILL_BLOCKED)
def test_deny_existing_capability_not_degraded(cmd):
    from app.services.execution_provider import _check_dangerous, _classify_risk

    assert _check_dangerous(cmd) is not None, f"原有拦截能力退化: {cmd}"
    assert _classify_risk(cmd, "bash") == "L5", f"原有 L5 判定退化: {cmd}"


@pytest.mark.parametrize("cmd", _MUST_PASS)
def test_deny_regex_does_not_overblock(cmd):
    from app.services.execution_provider import _check_dangerous

    assert _check_dangerous(cmd) is None, f"合法命令被误拦: {cmd}"


def test_deny_regex_specific_file_chmod_stays_l4():
    """chmod/chown 针对具体文件仍是 L4（不因正则升级被抬成 L5）。"""
    from app.services.execution_provider import _classify_risk

    assert _classify_risk("chmod 777 /srv/app/main.py", "bash") == "L4"
    assert _classify_risk("chown deploy:deploy /srv/app", "bash") == "L4"


# ── R18-1-04：D-034 L5 高风险命令用户 Gate ────────────────────────────────

def test_provider_l5_block_reports_gate_required():
    """三个 Provider 的 L5 阻断返回都带 gate_required=True（供上层建 Gate）。"""
    import asyncio

    from app.services.execution_provider import (
        ContainerExecutionProvider,
        LocalSubprocessExecutionProvider,
        WorkspaceLocalExecutionProvider,
    )

    for provider in (LocalSubprocessExecutionProvider(),
                     WorkspaceLocalExecutionProvider(),
                     ContainerExecutionProvider()):
        r = asyncio.run(provider.execute("sudo rm -rf /", language="bash", timeout=5))
        assert r["blocked"] is True, provider.name
        assert r["risk_level"] == "L5", provider.name
        assert r["gate_required"] is True, f"{provider.name} 未上报 gate_required"


def test_provider_l1_execution_has_no_gate_required():
    """L1 正常执行不带 gate_required（不误建 Gate）。"""
    import asyncio

    from app.services.execution_provider import LocalSubprocessExecutionProvider

    r = asyncio.run(LocalSubprocessExecutionProvider().execute(
        "echo r18-ok", language="bash", timeout=10))
    assert r["blocked"] is False
    assert r["risk_level"] == "L1"
    assert r.get("gate_required") in (None, False)
    assert "r18-ok" in r["stdout"]


def test_l5_command_creates_real_decidable_gate(isolated_data):
    """L5 槽位 → 真实落库、可裁决的 l5_high_risk_command Gate（非伪造）。"""
    from app.dependencies import get_services
    from app.graph.stage_handlers import _create_l5_command_gates
    from app.schemas.gate import GateDecisionRequest

    pid = "r18-l5-gate"
    details = [{"slot_id": "build_verified", "command": "sudo rm -rf /",
                "risk_level": "L5", "gate_required": True, "status": "needs_user_input"}]
    gate_ids = _create_l5_command_gates(pid, "run-r18-l5", details)

    assert len(gate_ids) == 1, "L5 命中须真实创建 1 个 Gate"
    gs = get_services().gate_service
    gate = gs.get(gate_ids[0])
    assert gate is not None, "Gate 必须真实落库"
    assert gate.gate_type == "l5_high_risk_command"
    assert gate.risk_level == "L5"
    assert gate.gate_status == "waiting_decision", "必须处于可裁决状态"
    assert gate.stage == "p5" and gate.run_id == "run-r18-l5"
    assert "sudo rm -rf /" in (gate.reason or ""), "须附被拦命令原文"
    assert gate.summary, "须附风险说明"
    # 槽位明细回填 gate_id（随 p5_validation_report.json 持久化，可追溯）
    assert details[0]["l5_gate_id"] == gate_ids[0]
    # 用户可真实裁决（走通用 Gate 决策内核）
    decided, _ = gs.decide(gate_ids[0], GateDecisionRequest(decision="reject",
                                                           reason="L5 不批准"),
                           drive_promotion=False)
    assert decided is not None and decided.gate_status == "rejected"


def test_l4_command_creates_no_gate(isolated_data):
    """L4 命中保持现状：不创建 Gate（D-034 只规定 L5，本轮不做 L4 扩展）。"""
    from app.dependencies import get_services
    from app.graph.stage_handlers import _create_l5_command_gates

    pid = "r18-l4-nogate"
    details = [{"slot_id": "build_verified", "command": "chmod 755 build.sh",
                "risk_level": "L4", "gate_required": True, "status": "needs_user_input"}]
    gate_ids = _create_l5_command_gates(pid, "run-r18-l4", details)

    assert gate_ids == [], "L4 不得创建 Gate"
    assert get_services().gate_service.list_by_project(pid) == []
    assert "l5_gate_id" not in details[0]


def test_l1_l2_l3_slots_create_no_gate(isolated_data):
    """L1/L2/L3 正常槽位不创建 Gate。"""
    from app.dependencies import get_services
    from app.graph.stage_handlers import _create_l5_command_gates

    pid = "r18-l123-nogate"
    details = [
        {"slot_id": "build_verified", "command": "echo build", "risk_level": "L1",
         "gate_required": False, "status": "validated"},
        {"slot_id": "tests_pass", "command": "npm test", "risk_level": "L3",
         "gate_required": False, "status": "validated"},
    ]
    assert _create_l5_command_gates(pid, "run-r18-l123", details) == []
    assert get_services().gate_service.list_by_project(pid) == []


def test_p5_handler_wires_l5_gate_end_to_end(isolated_data, monkeypatch):
    """端到端接线：真实 P4 产物 → 真实 Provider 拦截 L5 → 真实 RealP5Handler → 真实 Gate 落库。

    唯一注入点是"被检测出的构建命令"（模拟某工程检测到高风险命令）；其后执行、
    风险分级、槽位映射、Gate 创建全部走真实生产路径（无 mock，D-097）。
    P4 输入用与 test_r175_p5_r4_organic_green 相同的合法产物集构造法（真实落盘 + 真实 Gate approve）。
    """
    import asyncio
    import hashlib

    from app.core.database import get_session
    from app.dependencies import get_services
    from app.graph.stage_handlers import RealP5Handler
    from app.models.task_node_run import TaskNodeRun
    from app.schemas.gate import GateDecisionRequest
    from app.services import workspace_service
    from app.services.p5_command_service import P5CommandDetectionService, P5DetectedCommands

    pid = "r18-p5-l5-e2e"
    run_id = "run-r18-e2e"
    workspace_service.init_workspace(pid)
    ws = workspace_service.workspace_path(pid)

    # ① 合法 P4 产物集（output_code + patch + 真实 AET evidence + summary + task_node_run）
    (ws / "output_code").mkdir(parents=True, exist_ok=True)
    (ws / "patches").mkdir(parents=True, exist_ok=True)
    (ws / "artifacts" / "p4").mkdir(parents=True, exist_ok=True)
    out_rel = "output_code/migrate_service.py"
    (ws / out_rel).write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    out_sha = hashlib.sha256((ws / out_rel).read_bytes()).hexdigest()
    patch_rel = "patches/migrate_service.diff"
    (ws / patch_rel).write_text("--- a/legacy.py\n+++ b/output_code/migrate_service.py\n@@\n+x\n",
                                encoding="utf-8")
    ev_id = "ev-p4-r18-l5"
    get_services().aet_service.write_evidence(
        pid, ev_id, evidence_type="build_evidence", status="validated",
        source="p4_work_agent", claim="P4 产出 migrate_service.py", stage="p4",
        extra={"output_code_ref": out_rel, "output_sha256": out_sha})
    (ws / "artifacts" / "p4" / "p4_execution_summary.json").write_text(json.dumps({
        "stage": "p4", "graph_status": "completed", "node_count": 1,
        "change_manifest": [{"path": out_rel, "action": "created"}],
        "patch_index": [{"path": patch_rel, "node_id": "tn-001"}],
        "evidence_refs": [ev_id],
    }, ensure_ascii=False), encoding="utf-8")
    db = get_session()
    try:
        db.add(TaskNodeRun(node_id="tn-001", task_graph_run_id=run_id, project_id=pid,
                           run_id=run_id, stage="p4", node_status="completed",
                           artifact_refs=[out_rel, patch_rel], evidence_refs=[ev_id]))
        db.commit()
    finally:
        db.close()

    # ② 真实 P4→P5 晋级 Gate 并 approve
    gs = get_services().gate_service
    promo = gs.create(project_id=pid, run_id=run_id, stage="p4",
                      gate_type="stage_promotion", reason="P4 完成，晋级 P5",
                      artifact_refs=["artifacts/p4/p4_execution_summary.json"])
    gs.decide(promo.gate_id, GateDecisionRequest(decision="approve", reason="批准"),
              drive_promotion=False)

    # ③ 唯一注入点：构建命令被检测为高风险命令（其余全真实）
    def _fake_detect(self, project_id, *a, **kw):  # noqa: ANN001
        return P5DetectedCommands(project_id=project_id, project_type="custom",
                                  build_cmd="sudo rm -rf /", run_cmd=None,
                                  test_cmd=None, static_check_cmd=None,
                                  all_identified=False)

    monkeypatch.setattr(P5CommandDetectionService, "detect_commands", _fake_detect)

    result = asyncio.run(RealP5Handler().execute({"project_id": pid, "run_id": run_id}))

    build = [d for d in result.get("conditional_results", [])
             if d["slot_id"] == "build_verified"]
    assert build, f"P5 应走到条件槽阶段，实际 status={result.get('status')} / {result.get('reason')}"
    assert build[0]["risk_level"] == "L5"
    assert build[0]["gate_required"] is True
    gate_ids = result.get("l5_command_gate_ids") or []
    assert len(gate_ids) == 1, f"L5 命中须真实建 Gate，实际 {gate_ids}"
    gate = gs.get(gate_ids[0])
    assert gate.gate_type == "l5_high_risk_command"
    assert gate.gate_status == "waiting_decision"
    assert gate.project_id == pid
    # L5 被拦 → P5 不得伪造 completed
    assert result["status"] != "completed", "L5 命令未裁决时 P5 不得 completed"
    # gate_id 随验证报告持久化，可追溯
    report = json.loads((ws / "artifacts" / "p5_validation_report.json").read_text(encoding="utf-8"))
    persisted = [d for d in report["conditional_results"] if d["slot_id"] == "build_verified"]
    assert persisted and persisted[0].get("l5_gate_id") == gate_ids[0]
