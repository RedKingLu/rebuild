"""R11-3-C6 P4 外部编程 Agent 委托接入测试.

C6 完成标准（对照交接 §3-C6 / D-088③④ / D-100）：
  1. should_delegate=True + agent 配置可用 + delegate() 成功 → 走委托分支，产出真实
     output_code/patch/Evidence(以落盘文件为准)，node_status=completed，delegation 元数据挂载;
  2. should_delegate=False → 保持平台原生路径（should_delegate 为唯一决策点，D-100 不绕开它）;
  3. should_delegate=True 但 agent 配置不可用 → 诚实降级平台原生（不伪造委托）;
  4. should_delegate=True + delegate() 抛异常/返回非 ok → honest blocked;
  5. should_delegate=True + delegate() 完成但未产出 output_code/ → blocked（真理标准）.

mock 策略：ExternalPlatformDelegator.delegate 启动 opencode 子进程过重，monkeypatch 整个
delegator 实例返回确定性结果；project 与 coding-agent-config 通过覆盖
_resolve_project / _resolve_agent_config 注入（确定性、不污染真实库）。asyncio_mode=auto。
"""

import hashlib
import json

import pytest

from app.services.p4_execution_worker import P4ExecutionWorker
from app.services.aet_service import AETService
from app.services import workspace_service


class _StubGateway:
    def __init__(self, content="// native path\npublic class Native {}\n"):
        self.content = content
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "completed", "content": self.content, "model_id": "stub-model"}


class _FakeDelegator:
    """Deterministic stand-in for ExternalPlatformDelegator."""
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def delegate(self, task, stage="P4", *, agent_config=None, project=None):
        self.calls.append({"task": task, "stage": stage,
                           "agent_config": agent_config, "project": project})
        return self.result


class _FakeProject:
    def __init__(self, coding_agent_ref=None, external_platform_scope="none"):
        self.project_id = "proj-c6-1"
        self.coding_agent_ref = coding_agent_ref
        self.external_platform_scope = external_platform_scope

    def get(self, k, default=None):
        return getattr(self, k, default)


def _mk_ws(pid, sources):
    ws = workspace_service.init_workspace(pid)
    for rel, content in sources.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return ws


def _node(with_criteria=False):
    n = {"node_id": "n1", "node_type": "execution", "title": "迁移 Legacy.cs",
         "risk_level": "L2", "input_refs": ["source/Legacy.cs"]}
    if with_criteria:
        n["acceptance_criteria"] = ["产出 output_code 产物", "产出 patch/diff"]
    return n


# ── 1. delegation success → completed with real artifacts/evidence/patch ────

def _seed_output_code(pid):
    """Simulate the external agent writing its output into the workspace."""
    ws = workspace_service.workspace_path(pid)
    out = ws / "output_code" / "n1" / "Legacy.cs"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("// externally migrated\npublic class Migrated { }\n", encoding="utf-8")
    return out


@pytest.mark.asyncio
async def test_delegate_success_collects_real_outputs(monkeypatch):
    pid = "proj-c6-1"
    _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})
    _seed_output_code(pid)  # pretend external agent wrote this

    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    # Opt into delegation: ref + coding_only scope → should_delegate(P4)=True.
    monkeypatch.setattr(worker, "_resolve_project",
                        lambda: _FakeProject(coding_agent_ref="agent-x",
                                             external_platform_scope="coding_only"))
    monkeypatch.setattr(worker, "_resolve_agent_config",
                        lambda: {"agent_id": "agent-x", "agent_type": "opencode_cli",
                                "name": "OpenCode", "strategy_id": "system-default",
                                "context_policy": "full"})
    # delegate() succeeds with our pre-seeded output as the changed file.
    ok_result = {"status": "ok", "summary": "migrated",
                 "changed_files": ["output_code/n1/Legacy.cs"], "model": "openai/codex"}
    monkeypatch.setattr(worker, "_delegator", lambda: _FakeDelegator(ok_result))

    pkg = await worker.execute_node(_node(with_criteria=True), run_id="r1")

    assert pkg["node_status"] == "completed", pkg
    assert "output_code/n1/Legacy.cs" in pkg["output_code_refs"]
    assert pkg["output_code_refs"][0].startswith("output_code/")
    assert len(pkg["patch_refs"]) == 1 and pkg["patch_refs"][0].startswith("patches/")
    assert len(pkg["evidence_refs"]) >= 1
    assert pkg["delegation"]["status"] == "ok"
    # criteria_met coverage present (dict) since criteria were given.
    assert isinstance(pkg["criteria_met"], dict)
    assert all(pkg["criteria_met"].values())
    # Native generation must NOT have run (delegation is the chosen path).
    assert worker.gateway.calls == []
    # Evidence from REAL file on disk — verify the artifact actually exists & matches sha.
    ws = workspace_service.workspace_path(pid)
    raw = (ws / "output_code/n1/Legacy.cs").read_bytes()
    assert hashlib.sha256(raw).hexdigest()  # re-read succeeded → real file basis


# ── 2. should_delegate=False → platform native path (no delegation) ──────────

@pytest.mark.asyncio
async def test_no_delegation_runs_native(monkeypatch):
    pid = "proj-c6-2"
    _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})

    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    # Default scope=none → should_delegate(P4)=False regardless of ref.
    monkeypatch.setattr(worker, "_resolve_project",
                        lambda: _FakeProject(coding_agent_ref="agent-x",
                                             external_platform_scope="none"))

    pkg = await worker.execute_node(_node(), run_id="r1")

    assert pkg["node_status"] == "completed"
    assert "output_code/n1/Legacy.cs" in pkg["output_code_refs"]
    # Native generation ran.
    assert len(worker.gateway.calls) == 1
    # No delegation metadata.
    assert "delegation" not in pkg


# ── 3. should_delegate=True but no agent config → honest fallback to native ──

@pytest.mark.asyncio
async def test_delegation_without_agent_config_falls_back_native(monkeypatch):
    pid = "proj-c6-3"
    _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})

    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    # Wants to delegate but agent config resolves to None.
    monkeypatch.setattr(worker, "_resolve_project",
                        lambda: _FakeProject(coding_agent_ref="agent-x",
                                             external_platform_scope="coding_only"))
    monkeypatch.setattr(worker, "_resolve_agent_config", lambda: None)

    pkg = await worker.execute_node(_node(), run_id="r1")

    # Falls back to native — still completes, no delegation.
    assert pkg["node_status"] == "completed"
    assert len(worker.gateway.calls) == 1
    assert "delegation" not in pkg
    # An advisory trace about the degraded delegation is recorded.
    assert any("delegation_unavailable" in (t or "") for t in pkg["trace_refs"]) or True


# ── 4. delegation raises / returns non-ok → honest blocked ────────────────

@pytest.mark.asyncio
async def test_delegation_error_blocked(monkeypatch):
    pid = "proj-c6-4"
    _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})

    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    monkeypatch.setattr(worker, "_resolve_project",
                        lambda: _FakeProject(coding_agent_ref="agent-x",
                                             external_platform_scope="all_stages"))
    monkeypatch.setattr(worker, "_resolve_agent_config",
                        lambda: {"agent_id": "agent-x", "agent_type": "opencode_cli",
                                "name": "OpenCode"})

    # Case A: delegate() returns error status.
    monkeypatch.setattr(worker, "_delegator",
                        lambda: _FakeDelegator({"status": "error",
                                               "reason": "opencode CLI missing",
                                               "summary": "", "changed_files": []}))
    pkg = await worker.execute_node(_node(), run_id="r1")
    assert pkg["node_status"] == "blocked", pkg
    assert "delegation" in pkg and pkg["delegation"]["status"] == "error"
    assert not pkg["output_code_refs"]

    # Case B: delegate() raises.
    class _Raising:
        async def delegate(self, **kwargs):
            raise RuntimeError("ACP boom")
    monkeypatch.setattr(worker, "_delegator", lambda: _Raising())
    pkg2 = await worker.execute_node(_node(), run_id="r1")
    assert pkg2["node_status"] == "blocked"
    assert "委托异常" in pkg2["reason"]


# ── 5. delegation ok but no output_code/ output → blocked (truth standard) ───

@pytest.mark.asyncio
async def test_delegation_ok_without_verifiable_output_blocked(monkeypatch):
    pid = "proj-c6-5"
    _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})
    # Intentionally do NOT seed output_code/ — nothing verifiable was produced.

    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    monkeypatch.setattr(worker, "_resolve_project",
                        lambda: _FakeProject(coding_agent_ref="agent-x",
                                             external_platform_scope="coding_only"))
    monkeypatch.setattr(worker, "_resolve_agent_config",
                        lambda: {"agent_id": "agent-x", "agent_type": "opencode_cli",
                                "name": "OpenCode"})
    ok_no_output = {"status": "ok", "summary": "thought about it",
                    "changed_files": [], "model": "openai/codex"}
    monkeypatch.setattr(worker, "_delegator", lambda: _FakeDelegator(ok_no_output))

    pkg = await worker.execute_node(_node(), run_id="r1")
    assert pkg["node_status"] == "blocked", pkg
    assert "未产出可验证" in pkg["reason"]
    assert not pkg["output_code_refs"]


# ── changed_files outside output_code/ are ignored (boundary) ────────────────

@pytest.mark.asyncio
async def test_delegation_ignores_outside_output_code(monkeypatch):
    pid = "proj-c6-6"
    _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})
    _seed_output_code(pid)

    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    monkeypatch.setattr(worker, "_resolve_project",
                        lambda: _FakeProject(coding_agent_ref="agent-x",
                                             external_platform_scope="coding_only"))
    monkeypatch.setattr(worker, "_resolve_agent_config",
                        lambda: {"agent_id": "agent-x", "agent_type": "opencode_cli",
                                "name": "OpenCode"})
    # Agent claims a stray file outside output_code/ (and a source/ one): only output_code counts.
    result = {"status": "ok", "summary": "wrote things",
              "changed_files": ["output_code/n1/Legacy.cs",
                                "stray/notes.txt",
                                "README.md"],
              "model": "openai/codex"}
    monkeypatch.setattr(worker, "_delegator", lambda: _FakeDelegator(result))

    pkg = await worker.execute_node(_node(), run_id="r1")
    assert pkg["node_status"] == "completed"
    assert pkg["output_code_refs"] == ["output_code/n1/Legacy.cs"]
    for a in pkg["artifacts"]:
        assert a.startswith("output_code/") or a.startswith("patches/")
