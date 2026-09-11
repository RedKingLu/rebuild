"""B-RW-P4WORKER-WRITE-UNGUARDED（D-P0-01 第三入口）：`P4ExecutionWorker._write()` 是
工具循环结束后、模型最终轮次原始文本直写落盘的路径（:1049 单文件 / :1194 多文件"保留最终
文本为额外文件"），此前完全未接 `content_validity.detect_protocol_leak`。批次 A/E 已把同一
检查接到 `tool_registry.py` 的三处工具调用入口（`_execute_workspace_write` /
`_execute_generate_patch` / `_execute_apply_patch`），但那三处都是"模型显式调工具"路径——
`_write()` 是模型不调工具、直接把最终答案原文当产物落盘的第三条路径，三处已修复的拦截对
它完全不设防。详见 `证据/进度追踪/02-阻塞项.md` 该条目 + `产物/草稿/
V26.2-总验收真跑-问题梳理与返工修复计划.md` D-P0-01。

本文件覆盖：

  A. `_write()` 直接单测：内容合法性硬拦 + 防误伤 + 与 D-099①边界拒绝的可区分信号
     （`ProtocolLeakRejected`，ValueError 子类，携带 `content_validity.PROTOCOL_LEAK_
     REASON_CODE`）+ `check_content=False` opt-out（唯一用于节点续跑标记，见 C 段）。
  B. 单文件路径（`_execute_node_impl` 内 `_write(out_target, output_code, …)`）：模型
     最终轮次文本（非工具调用）含协议标记 → 不落盘、不报 completed，本轮零真实产出
     → blocked（携 rejected_writes + policy_forbidden，复用批次B已建立的机制，不另造）。
  C. 多文件路径（`_finalize_multifile` 内"保留最终文本为额外文件"分支）：同一污染 + 本轮
     还有其它工具调用产出的真实文件 → partial（不是 blocked：blocked 专留给"零真实产出"）。
     另覆盖：施工过程中核实发现的自引用边界情形——partial 节点的续跑标记 JSON 会诊断性地
     引用被拒的 marker 原文，若对标记 JSON 也无差别地跑同一检查，会自我触发拒写、丢失续
     跑标记（已用 `content_validity.detect_protocol_leak` 直接验证过这个自引用命中）；
     `_write_node_marker` 因此显式传 `check_content=False`（唯一的检查豁免点），本节验证
     该豁免不会绕开对 output_code/ 主内容的拦截，只豁免审计痕迹本身。
  D. 防回归：既有 completed（单文件+多文件，含 :1075/:1245 的 diff 写入、:1183 的
     fence-clean 重写）路径不受影响；D-099① source/ 边界拒绝仍是裸 ValueError（与
     ProtocolLeakRejected 可区分）。

判据设计与零误伤边界见 `app/services/content_validity.py` 模块 docstring（本文件只 import
复用该函数与常量，不重复其正则判据 — B-R20-REDACT-THREE-IMPLS 的教训）。call_stream 桩的
形态对齐既有 `tests/test_rw_node_completion_content_gate.py`（同一批次已建立的测试范式）。

【重要】本文件已写好但**未执行**：施工时有另一个全量 pytest 在后台运行，本项目历史上因
"多个 pytest 并发跑导致磁盘 I/O 踩踏 / DB 隔离守卫触发"翻过车（B-DB-ISOLATION-1 教训），
施工纪律要求在主全量测试跑完前不得起任何 pytest（含本文件的定向跑）。所有验证均通过直接
Python 函数调用完成（见施工报告），逻辑已确认正确；请在主全量测试完成后代为执行本文件。
"""

import json as _json
import uuid

from app.services import workspace_service
from app.services.aet_service import AETService
from app.services.content_validity import PROTOCOL_LEAK_REASON_CODE, detect_protocol_leak
from app.services.p4_execution_worker import P4ExecutionWorker, ProtocolLeakRejected

# 真实规模真跑实测形态（判据①：成对全角竖线 <｜｜ident｜｜>，见 content_validity.py）。
_LEAK = '<｜｜DSML｜｜ invoke name="fs_write_artifact">'


def _seed_fs_write_tool():
    """Seed fs_write_artifact（write_scope=workspace, L2）供 tool loop 调用，供多文件路径
    的场景使用（对齐 tests/test_rw_node_completion_content_gate.py 的既有做法）。"""
    from app.core.database import get_session
    from app.models.resource_entry import (ResourceEntry, ResourceType, SourceType,
                                            TrustLevel, RiskLevel, ResourceStatus)
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


def _mk_worker(pid: str, gateway=None) -> P4ExecutionWorker:
    workspace_service.init_workspace(pid)
    return P4ExecutionWorker(pid, aet=AETService(None), gateway=gateway)


# ══════════════════════════════════════════════════════════════════════════
# A. `_write()` 直接单测：内容合法性硬拦 + 防误伤 + 可区分信号
# ══════════════════════════════════════════════════════════════════════════

def test_direct_write_rejects_protocol_leak_content():
    """`_write()` 待写入内容含协议标记 → 不落盘（磁盘上文件不存在）+ 抛出可区分信号
    ProtocolLeakRejected（携带 reason_code == content_validity.PROTOCOL_LEAK_REASON_CODE）。"""
    pid = "proj-rw-p4direct-leak"
    worker = _mk_worker(pid)
    ws = workspace_service.workspace_path(pid)
    try:
        worker._write("output_code/n1/Bad.cs", _LEAK, "r1", "n1", action="write_output_code")
        assert False, "应抛出 ProtocolLeakRejected"
    except ProtocolLeakRejected as e:
        assert e.reason_code == PROTOCOL_LEAK_REASON_CODE
        assert e.marker  # 命中标记原文非空
        assert e.rel_path == "output_code/n1/Bad.cs"
    assert not (ws / "output_code" / "n1" / "Bad.cs").exists()
    # 未落盘也不应留下空目录以外的任何产物（不创建目录都算合格，但更重要是文件不存在）。


def test_direct_write_allows_normal_content_with_invoke_parameter_keywords():
    """防误伤（对齐批次A/E已建立的用例）：内容含英文单词 invoke/parameter（真实 C# 方法
    调用/签名），但不带 <｜｜ident｜｜> 哨兵结构 —— 不得被拦，正常落盘。"""
    pid = "proj-rw-p4direct-ok"
    worker = _mk_worker(pid)
    ws = workspace_service.workspace_path(pid)
    body = "public void invoke(string parameter) { /* real migrated code */ }\n"
    rel, risk, audit_id = worker._write("output_code/n1/Good.cs", body, "r1", "n1",
                                        action="write_output_code")
    assert rel == "output_code/n1/Good.cs"
    assert (ws / "output_code" / "n1" / "Good.cs").read_text("utf-8") == body


def test_direct_write_source_boundary_rejection_is_plain_valueerror_not_protocol_leak():
    """防回归：D-099① 越权写 source/ 的既有拒绝仍是裸 ValueError，且不是 ProtocolLeakRejected
    的实例 —— 两类拒绝原因（边界问题 vs 内容合法性问题）在异常类型层面可区分。"""
    pid = "proj-rw-p4direct-boundary"
    worker = _mk_worker(pid)
    try:
        worker._write("source/hack.cs", "public class X {}", "r1", "n1",
                      action="write_output_code")
        assert False, "应抛出 ValueError（D-099① 边界拒绝）"
    except ProtocolLeakRejected:
        assert False, "D-099① 边界拒绝不应被误判为协议泄漏信号"
    except ValueError as e:
        assert not isinstance(e, ProtocolLeakRejected)
        assert "read-only" in str(e) or "只读" in str(e) or "source" in str(e).lower()


def test_protocol_leak_rejected_is_still_caught_by_bare_except_valueerror():
    """安全默认：若某调用方遗漏了专门的 `except ProtocolLeakRejected` 分支，只有裸
    `except ValueError` 时仍应能捕获（继承关系兜底，不会变成未捕获异常向上炸穿）。"""
    pid = "proj-rw-p4direct-fallback"
    worker = _mk_worker(pid)
    caught_as_plain_valueerror = False
    try:
        worker._write("output_code/n1/Bad2.cs", _LEAK, "r1", "n1", action="write_output_code")
    except ValueError:
        caught_as_plain_valueerror = True
    assert caught_as_plain_valueerror


def test_direct_write_check_content_false_bypasses_gate():
    """`check_content=False`（仅 `_write_node_marker` 使用的显式豁免）确实能绕开内容检查——
    验证该 opt-out 参数本身按预期工作（不是死代码）。豁免的合理性验证见下面的自引用场景。"""
    pid = "proj-rw-p4direct-checkfalse"
    worker = _mk_worker(pid)
    ws = workspace_service.workspace_path(pid)
    rel, risk, audit_id = worker._write(
        "artifacts/diag.json", _json.dumps({"marker_diag": _LEAK}), "r1", "n1",
        action="write_node_marker", check_content=False)
    assert (ws / rel).exists()


# ══════════════════════════════════════════════════════════════════════════
# B. 单文件路径（:1049 附近）：模型最终轮次文本（非工具调用）含协议标记
# ══════════════════════════════════════════════════════════════════════════

class _SingleFileLeakGateway:
    """模型全程未调用任何工具，直接把含协议标记的文本作为最终答案输出（真跑实测形态：
    工具循环结束后模型最终轮次原始文本被当作产物）。"""

    async def call_stream(self, *, messages, tools=None, **kwargs):
        for ch in _LEAK:
            yield {"type": "token", "content": ch}
        yield {"type": "done", "model": "fake-stream-model"}


async def test_singlefile_final_text_protocol_leak_yields_blocked_not_completed():
    """判断依据：单文件路径下 `_write(out_target, output_code, …)` 是本节点唯一的写入尝试
    （written_files 为空，否则会走 `_finalize_multifile`）——被拒即本轮零真实产出，判 blocked
    （不是 completed，也不应误标 scope_violation：这是内容合法性问题，非 D-099 越权写）。"""
    pid = "proj-rw-p4worker-sf-leak"
    worker = _mk_worker(pid, _SingleFileLeakGateway())
    node = {"node_id": "n1", "node_type": "execution", "title": "迁移 Legacy", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r1")

    assert pkg["node_status"] == "blocked"
    assert pkg["node_status"] != "completed"
    assert pkg.get("scope_violation") is not True  # 与 D-099① 边界拒绝的既有标记区分
    assert pkg.get("policy_forbidden") is True
    assert "rejected_writes" in pkg and len(pkg["rejected_writes"]) == 1
    rw = pkg["rejected_writes"][0]
    assert rw["marker"]
    assert pkg["output_code_refs"] == []
    assert pkg["artifacts"] == []
    # 未落盘：output_code/ 下不应出现任何该节点的产物文件。
    ws = workspace_service.workspace_path(pid)
    out_dir = ws / "output_code" / "n1"
    assert not out_dir.exists() or not any(out_dir.rglob("*"))


# ══════════════════════════════════════════════════════════════════════════
# C. 多文件路径（:1194 附近）："保留最终文本为额外文件" 分支含协议标记
# ══════════════════════════════════════════════════════════════════════════

class _TCFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _TC:
    """call_stream tool_calls 帧里的一个 tool call（结构对齐既有
    tests/test_rw_node_completion_content_gate.py 的桩）。"""

    def __init__(self, index, tid, name, arguments):
        self.index = index
        self.id = tid
        self.function = _TCFn(name, arguments)


class _MultiFileExtraLeakGateway:
    """第一轮：模型通过 fs_write_artifact 真实写入一个干净文件（Good.cs）。第二轮：模型不再
    调工具，最终文本是"保留为额外文件"分支要落盘的内容——本身以合法代码起始（能通过
    `_looks_like_code_start` 而被保留为额外文件），但内部嵌入协议标记。"""

    def __init__(self):
        self.round = 0

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            yield {"type": "tool_calls", "tool_calls": [
                _TC(0, "w1", "fs_write_artifact",
                    _json.dumps({"path": "output_code/n2/Good.cs",
                                "content": "public class Good {}\n"}))]}
            yield {"type": "done", "model": "fake-stream-model"}
        else:
            body = f"public class Malicious {{ {_LEAK} }}"
            for ch in body:
                yield {"type": "token", "content": ch}
            yield {"type": "done", "model": "fake-stream-model"}


async def test_multifile_extra_final_text_protocol_leak_yields_partial_with_existing_output():
    """判断依据：本轮除被拒的"额外文件"外，Good.cs 是工具调用真实成功落盘的产出
    （produced 非空）——节点本轮存在真实成功产出，因此不应判 blocked（会错误抹掉真实产出）；
    但也不应笼统报 completed（会掩盖"曾经发生的拒写"）——判 partial，且 output_code_refs
    只含真正成功的文件，rejected_writes 如实记录被拒的额外文件、policy_forbidden=True
    （复用批次B已建立的机制，未新造判定逻辑）。"""
    pid = "proj-rw-p4worker-mf-leak"
    _seed_fs_write_tool()
    worker = _mk_worker(pid, _MultiFileExtraLeakGateway())
    node = {"node_id": "n2", "node_type": "execution", "title": "迁移 多文件", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r2")

    assert pkg["node_status"] == "partial"
    assert pkg["node_status"] != "completed"
    assert pkg.get("multi_file") is True
    assert pkg["output_code_refs"] == ["output_code/n2/Good.cs"]
    assert pkg.get("policy_forbidden") is True
    assert len(pkg["rejected_writes"]) == 1
    ws = workspace_service.workspace_path(pid)
    assert (ws / "output_code" / "n2" / "Good.cs").read_text("utf-8") == "public class Good {}\n"
    # 被拒的"额外文件"内容（含协议标记）绝不落盘为任何文件。
    assert not any(
        f.name not in ("Good.cs",) for f in (ws / "output_code" / "n2").iterdir()
    )


async def test_partial_node_marker_still_persists_despite_diagnostic_quote_of_rejected_marker():
    """施工过程中核实发现的自引用边界：partial 节点的续跑标记 JSON 会诊断性地引用被拒的
    marker 原文（`rejected_writes[i]["marker"]`），若不豁免会自我触发拒写、丢失续跑标记
    （已用 detect_protocol_leak 直接验证过这个自引用命中）。`_write_node_marker` 显式传
    check_content=False，本用例验证该豁免生效：即便 package 里带着"协议标记原文"的诊断
    引用，标记文件本身仍应真实落盘（不因自我引用而被误拒），但被拒的原始内容仍然不会作为
    output_code/ 产物落盘（豁免只发生在 marker 这一层审计痕迹，不豁免主内容关卡）。"""
    pid = "proj-rw-p4worker-mf-leak-marker"
    _seed_fs_write_tool()
    worker = _mk_worker(pid, _MultiFileExtraLeakGateway())
    node = {"node_id": "n2", "node_type": "execution", "title": "迁移 多文件", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r-marker")

    assert pkg["node_status"] == "partial"
    ws = workspace_service.workspace_path(pid)
    marker_path = ws / "artifacts" / "p4_nodes" / "r-marker" / "n2.done.json"
    assert marker_path.is_file(), "partial 节点的续跑标记应真实落盘（check_content=False 生效）"
    marker_content = marker_path.read_text("utf-8")
    # 标记内容如实携带诊断引用（含协议标记原文片段），证明这确实是自引用场景，而非误配置。
    assert "DSML" in marker_content
    # 但真正的恶意内容（完整污染文本）从未作为 output_code/ 产物落盘。
    bad_files = [f for f in (ws / "output_code" / "n2").iterdir() if f.name != "Good.cs"]
    assert bad_files == []


# ══════════════════════════════════════════════════════════════════════════
# D. 防回归：既有 completed 路径（含 :1075/:1245 diff 写入、:1183 fence-clean 重写、
#    :291 节点标记）不受影响
# ══════════════════════════════════════════════════════════════════════════

class _CleanSingleFileGateway:
    """回归对照：全程无任何拒写，模型直接给出真实迁移代码文本（既有 completed 判定路径，
    行经 :1049 主写入 + :1075 diff 写入 + :291 节点标记，三处均应正常放行）。"""

    async def call_stream(self, *, messages, tools=None, **kwargs):
        body = "public class Clean { public int A { get; set; } }\n"
        for ch in body:
            yield {"type": "token", "content": ch}
        yield {"type": "done", "model": "fake-stream-model"}


async def test_singlefile_clean_completion_unaffected():
    """回归：单文件路径的既有 completed 判定不受本次改动影响，且节点标记真实落盘。"""
    pid = "proj-rw-p4worker-sf-clean"
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
    patch_ref = pkg["patch_refs"][0]
    assert (ws / patch_ref).is_file()  # :1075 diff 写入正常放行
    marker_path = ws / "artifacts" / "p4_nodes" / "r4" / "n4.done.json"
    assert marker_path.is_file()  # :291 节点标记正常放行（正常内容，check_content=False 亦无碍）


class _CleanMultiFileGateway:
    """回归对照：多文件路径全程无任何拒写（脚手架场景：模型只调工具写入文件，最终文本是
    摘要句而非代码，:1183 的 fence-clean 重写与 :1245 的 per-file diff 写入均应正常放行）。"""

    def __init__(self):
        self.round = 0

    async def call_stream(self, *, messages, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            yield {"type": "tool_calls", "tool_calls": [
                _TC(0, "w1", "fs_write_artifact",
                    _json.dumps({
                        "path": "output_code/n5/Program.cs",
                        # 故意带 markdown 围栏，触发 :1183 的 fence-clean 重写路径。
                        "content": "```csharp\npublic class Program { public static void Main() {} }\n```",
                    }))]}
            yield {"type": "done", "model": "fake-stream-model"}
        else:
            for ch in "已生成脚手架文件。":
                yield {"type": "token", "content": ch}
            yield {"type": "done", "model": "fake-stream-model"}


async def test_multifile_clean_completion_unaffected_including_fence_clean_rewrite():
    """回归：多文件路径的既有 completed 判定不受本次改动影响——包括 :1183 的
    fence-clean 重写（模型工具写入的内容带 markdown 围栏，清洗后重写）与 :1245 的
    per-file diff 写入，均应正常放行且产出正确内容；节点标记正常落盘。"""
    pid = "proj-rw-p4worker-mf-clean"
    _seed_fs_write_tool()
    worker = _mk_worker(pid, _CleanMultiFileGateway())
    node = {"node_id": "n5", "node_type": "execution", "title": "迁移 clean", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r5")

    assert pkg["node_status"] == "completed"
    assert "rejected_writes" not in pkg
    assert "policy_forbidden" not in pkg
    ws = workspace_service.workspace_path(pid)
    out_ref = pkg["output_code_refs"][0]
    # fence-clean 重写生效：磁盘上的内容已去除 markdown 围栏（_strip_code_fence 的
    # 单行 join 不补回结尾换行，故预期值不带尾随 \n —— 已用直接 Python 调用核实过实际
    # 落盘内容，而非凭猜测断言）。
    assert (ws / out_ref).read_text("utf-8") == (
        "public class Program { public static void Main() {} }")
    assert len(pkg["patch_refs"]) == 1
    assert (ws / pkg["patch_refs"][0]).is_file()
    marker_path = ws / "artifacts" / "p4_nodes" / "r5" / "n5.done.json"
    assert marker_path.is_file()


class _BlockedStreamGateway:
    """回归对照：既有"无接地诚实 blocked"路径（模型答 BLOCKED:，全程不涉及任何写入）
    不受本次改动影响。"""

    async def call_stream(self, *, messages, tools=None, **kwargs):
        for ch in "BLOCKED: 无法在 source/ 下定位到本节点应迁移的真实文件":
            yield {"type": "token", "content": ch}
        yield {"type": "done", "model": "fake-stream-model"}


async def test_honest_blocked_path_unaffected_by_direct_write_content_gate():
    """回归：既有"无接地诚实 blocked"路径不受本次改动影响（不因本次改动被误标
    rejected_writes/policy_forbidden）。"""
    pid = "proj-rw-p4worker-honest-blocked"
    worker = _mk_worker(pid, _BlockedStreamGateway())
    node = {"node_id": "n6", "node_type": "execution", "title": "迁移", "input_refs": []}
    pkg = await worker.execute_node(node, run_id="r6")

    assert pkg["node_status"] == "blocked"
    assert "rejected_writes" not in pkg
    assert pkg.get("policy_forbidden") is not True
    assert pkg["artifacts"] == []
