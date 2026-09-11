"""批次B（D-P0-01 漏网入口 + D-P0-02 节点完成判定 content gate）。

背景：真实规模真跑（1018 源文件）证据显示，模型工具调用协议原文（`<｜｜DSML｜｜ …>`）
被当作代码/补丁写入产出，而节点仍报 `completed`。`_execute_workspace_write` 已接
`content_validity.detect_protocol_leak` 硬拦，但同一污染还漏在另外两条写入路径：
`_execute_generate_patch`（写 patches/）与 `_execute_apply_patch`（写 output_code/）。
本文件覆盖：

  A. 两处漏网入口接入 detect_protocol_leak（拒写 + reason_code）+ 两处防误伤。
  B. D-P0-02：节点完成判定如实反映"本轮是否发生过拒写、是否还有其它真实产出"——
     本轮零真实产出 → blocked；本轮除拒写外还有其它真实成功产出 → partial（如实记录
     哪些被拒/哪些成功），既有的 completed / 其它 blocked 路径不受影响（回归）。

判据设计与零误伤边界见 `app/services/content_validity.py` 模块 docstring（本文件只
import 复用该函数，不重复其正则判据 —— B-R20-REDACT-THREE-IMPLS 的教训）。
"""

import json as _json

from app.services import tool_registry
from app.services.content_validity import PROTOCOL_LEAK_REASON_CODE
from app.services.p4_execution_worker import P4ExecutionWorker
from app.services.aet_service import AETService
from app.services import workspace_service


# 真实规模真跑实测形态（判据①：成对全角竖线 <｜｜ident｜｜>，见 content_validity.py）。
_LEAK = '<｜｜DSML｜｜ invoke name="fs_write_artifact">'


def _init_ws(pid: str):
    workspace_service.init_workspace(pid)
    return workspace_service.workspace_path(pid)


def _write_patch_draft_directly(ws, patch_rel: str, target_path: str, diff: str) -> None:
    """直接把补丁草案 envelope 写进 patches/，绕开 _execute_generate_patch（它现在也接了
    detect_protocol_leak——用它来"制造"一个内容已含协议标记的草案会在草案阶段就被拒写，
    测不到 apply_patch 自己的检查）。这样可以单独、干净地验证 apply_patch 落盘前的检查，
    不依赖 generate_patch 是否放行同一段文本。"""
    import json as _j
    p = ws / patch_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_j.dumps({"kind": "patch_draft", "target_path": target_path, "diff": diff},
                          ensure_ascii=False), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# A. _execute_generate_patch / _execute_apply_patch 接入 detect_protocol_leak
# ══════════════════════════════════════════════════════════════════════════

async def test_generate_patch_rejects_protocol_leak_diff():
    """漏网入口①：generate_patch 把含协议标记的 diff 原样写进 patches/ —— 须拒写。"""
    pid = "proj-genpatch-leak"
    ws = _init_ws(pid)
    diff = f"@@ -1 +1 @@\n-old\n+{_LEAK}\n"
    result = await tool_registry._execute_generate_patch(
        "generate_patch", {"target_path": "output_code/Foo.cs", "diff": diff}, pid, None)
    assert result["status"] == "rejected"
    assert result["reason_code"] == PROTOCOL_LEAK_REASON_CODE
    assert "error" in result
    # 未落盘：patches/ 目录不应产生任何草稿文件。
    patches_dir = ws / "patches"
    assert not patches_dir.exists() or not any(patches_dir.rglob("*"))


async def test_generate_patch_allows_normal_diff_with_invoke_keyword():
    """防误伤：diff 内容含英文单词 invoke/parameter（真实 C# 方法签名），
    但不带 <｜｜ident｜｜> 哨兵结构 —— 不得被拦。"""
    pid = "proj-genpatch-ok"
    _init_ws(pid)
    diff = ('@@ -1,2 +1,2 @@\n'
            ' public void invoke(string parameter) {\n'
            '-    Old();\n'
            '+    New();\n')
    result = await tool_registry._execute_generate_patch(
        "generate_patch", {"target_path": "output_code/Foo.cs", "diff": diff}, pid, None)
    assert result["status"] == "patch_drafted"
    assert result["patch_ref"].startswith("patches/")


async def test_apply_patch_rejects_protocol_leak_content():
    """漏网入口②：apply_patch 把补丁草案（整文件内容含协议标记）应用进 output_code/ —— 须拒写。

    草案直接写盘构造（不经 generate_patch）：generate_patch 现在也接了同一检查（Task A），
    若用它来生成一个"内容已含协议标记"的草案，会在草案阶段就被拒写，测不到 apply_patch
    自己落盘前的检查——这是两处检查独立生效的正确表现，但意味着这条用例必须绕开
    generate_patch 才能单独验证 apply_patch 这一侧。"""
    pid = "proj-applypatch-leak"
    ws = _init_ws(pid)
    _write_patch_draft_directly(ws, "patches/manual-bad.patch", "output_code/Bad.cs",
                                f"public class Bad {{ {_LEAK} }}")

    applied = await tool_registry._execute_apply_patch(
        "apply_patch_with_confirm",
        {"patch_ref": "patches/manual-bad.patch", "target_path": "output_code/Bad.cs"},
        pid, None)
    assert applied["status"] == "rejected"
    assert applied["reason_code"] == PROTOCOL_LEAK_REASON_CODE
    # output_code/Bad.cs 从未落盘。
    assert not (ws / "output_code" / "Bad.cs").exists()


async def test_apply_patch_allows_normal_content_with_invoke_keyword():
    """防误伤：补丁应用结果为真实代码（含 invoke/parameter 英文词）—— 不得被拦，正常落盘。"""
    pid = "proj-applypatch-ok"
    ws = _init_ws(pid)
    body = "public void invoke(string parameter) { /* real migrated code */ }\n"
    drafted = await tool_registry._execute_generate_patch(
        "generate_patch", {"target_path": "output_code/Good.cs", "diff": body}, pid, None)
    applied = await tool_registry._execute_apply_patch(
        "apply_patch_with_confirm",
        {"patch_ref": drafted["patch_ref"], "target_path": "output_code/Good.cs"},
        pid, None)
    assert applied["status"] == "patch_applied"
    assert (ws / "output_code" / "Good.cs").read_text("utf-8") == body


async def test_apply_patch_unified_diff_leak_in_applied_result_is_rejected():
    """漏网入口②的 unified-diff 分支：hunk 应用后的最终文本含协议标记同样须拒写
    （不仅整文件内容分支，unified diff 分支落盘前也过同一检查）。"""
    pid = "proj-applypatch-diffleak"
    ws = _init_ws(pid)
    base_target = ws / "output_code" / "greet.cs"
    base_target.parent.mkdir(parents=True, exist_ok=True)
    base_target.write_text("public class Greet {\n    // old\n}\n", encoding="utf-8")

    diff = "\n".join([
        "@@ -1,3 +1,3 @@",
        " public class Greet {",
        "-    // old",
        f"+    {_LEAK}",
        " }",
    ]) + "\n"
    # 同上：绕开 generate_patch（它现在也会拒写这段含协议标记的 diff），单独验证
    # apply_patch 的 unified-diff 分支——hunk 应用后的最终文本落盘前也过同一检查。
    _write_patch_draft_directly(ws, "patches/manual-diffleak.patch",
                                "output_code/greet.cs", diff)

    applied = await tool_registry._execute_apply_patch(
        "apply_patch_with_confirm",
        {"patch_ref": "patches/manual-diffleak.patch", "target_path": "output_code/greet.cs"},
        pid, None)
    assert applied["status"] == "rejected"
    assert applied["reason_code"] == PROTOCOL_LEAK_REASON_CODE
    # 目标文件未被污染改写，原内容保持不变（D-097：拒写不得腐化既有文件）。
    assert base_target.read_text("utf-8") == "public class Greet {\n    // old\n}\n"


# ══════════════════════════════════════════════════════════════════════════
# B. D-P0-02：节点完成判定 content gate
# ══════════════════════════════════════════════════════════════════════════

class _TCFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _TC:
    """call_stream tool_calls 帧里的一个 tool call（结构对齐 p4_execution_worker 的期望，
    与既有 tests/test_r11_c4_p4_worker.py 的桩保持同型）。"""

    def __init__(self, index, tid, name, arguments):
        self.index = index
        self.id = tid
        self.function = _TCFn(name, arguments)


def _seed_fs_write_tool():
    """Seed fs_write_artifact（write_scope=workspace, L2，不触发 Gate）供 tool loop 调用。"""
    from app.core.database import get_session
    from app.models.resource_entry import (ResourceEntry, ResourceType, SourceType,
                                            TrustLevel, RiskLevel, ResourceStatus)
    import uuid
    db = get_session()
    try:
        db.add(ResourceEntry(
            resource_id=str(uuid.uuid4()), name="fs_write_artifact", resource_type=ResourceType.tool,
            source_type=SourceType.internal_current, source_trust_level=TrustLevel.trusted_current,
            risk_level=RiskLevel.L2, status=ResourceStatus.active, enabled=True,
            description="写入 workspace 内产物（output_code/artifacts）",
            type_metadata={"tool_name": "fs_write_artifact", "write_scope": "workspace",
                           "parameters": {"type": "object",
                                          "properties": {"path": {"type": "string"},
                                                         "content": {"type": "string"}},
                                          "required": ["path", "content"]}}))
        db.commit()
    finally:
        db.close()


class _AllRejectedGateway:
    """本轮唯一一次写入尝试即被 detect_protocol_leak 拒写，随后模型未再产出任何内容
    （模拟"本轮零真实产出"——D-P0-02 核心场景：节点判定不应为 completed）。"""

    def __init__(self):
        self.round = 0

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            yield {"type": "tool_calls", "tool_calls": [
                _TC(0, "w1", "fs_write_artifact",
                    _json.dumps({"path": "output_code/n1/Bad.cs", "content": _LEAK}))]}
            yield {"type": "done", "model": "fake-stream-model"}
        else:
            yield {"type": "done", "model": "fake-stream-model"}


class _PartialMultiFileGateway:
    """本轮并行发起两次 fs_write_artifact：一次含协议标记（被拒），一次为真实代码（成功）。
    随后模型以一句摘要结束（不落为额外文件）。D-P0-02 部分成功场景（多文件路径）。"""

    def __init__(self):
        self.round = 0

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            yield {"type": "tool_calls", "tool_calls": [
                _TC(0, "w1", "fs_write_artifact",
                    _json.dumps({"path": "output_code/n2/Bad.cs", "content": _LEAK})),
                _TC(1, "w2", "fs_write_artifact",
                    _json.dumps({"path": "output_code/n2/Good.cs",
                                "content": "public class Good {}\n"})),
            ]}
            yield {"type": "done", "model": "fake-stream-model"}
        else:
            for ch in "已尝试写入 2 个文件（其中 1 个被拒）。":
                yield {"type": "token", "content": ch}
            yield {"type": "done", "model": "fake-stream-model"}


class _PartialSingleFileGateway:
    """单文件路径：模型先尝试 fs_write_artifact（含协议标记，被拒），放弃工具调用后把
    真实迁移代码直接作为最终文本回答输出（落盘走 _execute_node_impl 的单文件 _write()
    分支，而不是 _finalize_multifile）。D-P0-02 部分成功场景（单文件路径）。"""

    def __init__(self):
        self.round = 0

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            yield {"type": "tool_calls", "tool_calls": [
                _TC(0, "w1", "fs_write_artifact",
                    _json.dumps({"path": "output_code/n3/Legacy.cs", "content": _LEAK}))]}
            yield {"type": "done", "model": "fake-stream-model"}
        else:
            body = "public class Legacy2 { public int A { get; set; } }\n"
            for ch in body:
                yield {"type": "token", "content": ch}
            yield {"type": "done", "model": "fake-stream-model"}


class _CleanSingleFileGateway:
    """回归对照：全程无任何拒写，模型直接给出真实迁移代码文本（既有 completed 判定路径）。"""

    async def call_stream(self, *, messages, tools=None, **kwargs):
        body = "public class Clean { public int A { get; set; } }\n"
        for ch in body:
            yield {"type": "token", "content": ch}
        yield {"type": "done", "model": "fake-stream-model"}


class _BlockedStreamGateway:
    """回归对照：模型诚实报告无法接地（既有 BLOCKED: 路径），全程不涉及任何写入/拒写。"""

    async def call_stream(self, *, messages, tools=None, **kwargs):
        for ch in "BLOCKED: 无法在 source/ 下定位到本节点应迁移的真实文件":
            yield {"type": "token", "content": ch}
        yield {"type": "done", "model": "fake-stream-model"}


def _mk_worker(pid: str, gateway) -> P4ExecutionWorker:
    workspace_service.init_workspace(pid)
    return P4ExecutionWorker(pid, aet=AETService(None), gateway=gateway)


# ── D-P0-02 核心：本轮全部写入被拒 + 零真实产出 → 不报 completed ────────────

async def test_node_all_writes_rejected_yields_blocked_not_completed():
    """判断依据：本轮唯一的写入尝试被 detect_protocol_leak 拒写，且模型此后未产出任何
    可落盘的真实内容（tool_written_files 为空、最终文本为空）——节点在本轮里没有任何
    真实成功的动作，因此判定为 blocked（不是 partial：partial 专留给"除拒写外还有其它
    真实成功产出"的场景，见下面的 partial 测试）。"""
    pid = "proj-p0-02-blocked"
    _seed_fs_write_tool()
    worker = _mk_worker(pid, _AllRejectedGateway())
    node = {"node_id": "n1", "node_type": "execution", "title": "迁移 Bad", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r1")

    assert pkg["node_status"] == "blocked"
    assert pkg["node_status"] != "completed"
    assert "rejected_writes" in pkg and len(pkg["rejected_writes"]) == 1
    rw = pkg["rejected_writes"][0]
    assert rw["tool_name"] == "fs_write_artifact"
    assert rw["path"] == "output_code/n1/Bad.cs"
    # 拒写清单里不应包含被拒内容本身的敏感/污染文本被误当作"产物"落盘。
    ws = workspace_service.workspace_path(pid)
    assert not (ws / "output_code" / "n1" / "Bad.cs").exists()
    assert pkg["artifacts"] == []
    assert pkg["output_code_refs"] == []


# ── D-P0-02 部分成功：本轮部分写入被拒、部分成功 → 如实反映 + 记录细节 ──────

async def test_node_partial_writes_multifile_records_rejected_and_succeeded():
    """判断依据：本轮除被拒的 Bad.cs 外，Good.cs 真实成功落盘（_finalize_multifile 的
    produced 非空）——节点本轮存在真实成功产出，因此不应判 blocked（那会错误抹掉真实
    产出）；但也不应笼统报 completed（那会掩盖"曾经发生的拒写"）——判 partial，且
    output_code_refs 只含真正成功的文件，rejected_writes 如实记录被拒的那一个。"""
    pid = "proj-p0-02-partial-mf"
    _seed_fs_write_tool()
    worker = _mk_worker(pid, _PartialMultiFileGateway())
    node = {"node_id": "n2", "node_type": "execution", "title": "迁移 多文件", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r2")

    assert pkg["node_status"] == "partial"
    assert pkg["node_status"] != "completed"
    assert pkg.get("multi_file") is True
    # 如实记录：哪个成功 —— 只有 Good.cs 进入产出清单。
    assert pkg["output_code_refs"] == ["output_code/n2/Good.cs"]
    # 如实记录：哪个被拒。
    assert len(pkg["rejected_writes"]) == 1
    assert pkg["rejected_writes"][0]["path"] == "output_code/n2/Bad.cs"
    assert pkg["rejected_writes"][0]["tool_name"] == "fs_write_artifact"
    # policy_forbidden 复用 AcceptanceService 既有检查#7 —— 确保下游不会把 partial
    # 误路由为 accepted/completed（不新增/不改 acceptance_service.py 本身）。
    assert pkg.get("policy_forbidden") is True
    # 真实文件确实落盘，且被拒文件确实未落盘。
    ws = workspace_service.workspace_path(pid)
    assert (ws / "output_code" / "n2" / "Good.cs").read_text("utf-8") == "public class Good {}\n"
    assert not (ws / "output_code" / "n2" / "Bad.cs").exists()


async def test_node_partial_writes_singlefile_records_rejected_and_succeeded():
    """单文件路径的同一判据：模型工具调用被拒后，改用最终文本给出真实代码并成功落盘
    （_execute_node_impl 的单文件 _write() 分支，而非 _finalize_multifile）—— 同样应
    判 partial，而不是 completed 或 blocked。"""
    pid = "proj-p0-02-partial-sf"
    _seed_fs_write_tool()
    worker = _mk_worker(pid, _PartialSingleFileGateway())
    node = {"node_id": "n3", "node_type": "execution", "title": "迁移 Legacy", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r3")

    assert pkg["node_status"] == "partial"
    assert pkg["node_status"] != "completed"
    assert len(pkg["output_code_refs"]) == 1
    out_ref = pkg["output_code_refs"][0]
    ws = workspace_service.workspace_path(pid)
    assert (ws / out_ref).read_text("utf-8") == (
        "public class Legacy2 { public int A { get; set; } }\n")
    assert len(pkg["rejected_writes"]) == 1
    assert pkg["rejected_writes"][0]["path"] == "output_code/n3/Legacy.cs"
    assert pkg.get("policy_forbidden") is True


# ── 防回归：既有 completed / 其它 blocked 路径不受本次改动影响 ──────────────

async def test_node_normal_completion_unaffected_no_rejected_writes():
    """回归：全程无任何拒写的普通成功节点，仍判 completed，且不带 rejected_writes/
    policy_forbidden 字段（本次改动只在 rejected_writes 非空时才生效，不改变既有分支）。"""
    pid = "proj-p0-02-clean"
    worker = _mk_worker(pid, _CleanSingleFileGateway())
    node = {"node_id": "n4", "node_type": "execution", "title": "迁移 Clean", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r4")

    assert pkg["node_status"] == "completed"
    assert "rejected_writes" not in pkg
    assert "policy_forbidden" not in pkg
    ws = workspace_service.workspace_path(pid)
    out_ref = pkg["output_code_refs"][0]
    assert (ws / out_ref).read_text("utf-8") == (
        "public class Clean { public int A { get; set; } }\n")


async def test_node_honest_blocked_path_unaffected_by_content_gate():
    """回归：既有"无接地诚实 blocked"路径（模型答 BLOCKED:，全程无写入/无拒写）不受
    本次改动影响 —— 仍判 blocked，且不因本次改动被误标 rejected_writes。"""
    pid = "proj-p0-02-honest-blocked"
    worker = _mk_worker(pid, _BlockedStreamGateway())
    node = {"node_id": "n5", "node_type": "execution", "title": "迁移", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r5")

    assert pkg["node_status"] == "blocked"
    assert "rejected_writes" not in pkg
    assert pkg["artifacts"] == []
