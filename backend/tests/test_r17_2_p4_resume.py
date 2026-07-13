"""R17.2 OD-07 — P4 节点级幂等续跑（服务器重启不丢进度）测试.

修复目标（对照本轮交接 OD-07）：
  - 节点成功 completed 后，落一个磁盘 done 标记（键 = run_id + node_id），
    经 WorkspaceMediator 落盘（D-099，绝不写 source/）。
  - execute_node 入口：done 标记存在 **且** 引用的真实产物文件仍在磁盘、
    sha256/bytes 与标记记录一致 → 直接重建 completed 包（resumed=True），
    跳过 LLM 重算（续跑）。
  - 标记存在但引用文件缺失/校验不符 → 不信任标记，正常重新执行（诚实，不伪造）。

asyncio_mode=auto（与 test_r11_c4_p4_worker.py 一致，async 用例无需装饰器）。
"""

from app.services.p4_execution_worker import P4ExecutionWorker
from app.services.aet_service import AETService
from app.services import workspace_service


class _CountingGateway:
    """确定性 ModelGateway：返回 completed + 固定内容，并对 call() 计数。

    用于断言续跑分支是否真的跳过了 _generate（即是否调用了 gateway）。
    """

    def __init__(self, content="// migrated to target stack\npublic class Migrated {}\n"):
        self.content = content
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "completed", "content": self.content, "model_id": "stub-model"}


def _mk_ws(pid: str, sources: dict[str, str]):
    ws = workspace_service.init_workspace(pid)
    for rel, content in sources.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return ws


def _worker(pid, gateway):
    return P4ExecutionWorker(pid, aet=AETService(None), gateway=gateway)


# ── 用例1：命中 done 标记 → 续跑，跳过 _generate ─────────────────────────────

async def test_resume_hits_marker_and_skips_generate():
    pid = "proj-r172-resume-1"
    ws = _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})
    node = {"node_id": "n1", "node_type": "execution", "title": "迁移 Legacy.cs",
            "risk_level": "L2", "input_refs": ["source/Legacy.cs"]}

    # 第一次执行：真实生成 + 落产物 + 落 done 标记
    gw1 = _CountingGateway()
    pkg1 = await _worker(pid, gw1).execute_node(node, run_id="run-A")
    assert pkg1["node_status"] == "completed"
    assert len(gw1.calls) == 1                       # 首次真实调用了模型
    assert not pkg1.get("resumed")                    # 首次不是续跑
    out_ref = pkg1["output_code_refs"][0]
    patch_ref = pkg1["patch_refs"][0]
    assert (ws / out_ref).is_file()
    assert (ws / patch_ref).is_file()
    # done 标记真实落盘（artifacts/ 下，经 mediator）
    marker = ws / "artifacts" / "p4_nodes" / "run-A" / "n1.done.json"
    assert marker.is_file(), "首次完成后应落 done 标记"

    # 第二次执行（模拟重启后同 run_id/node_id）：应命中续跑，绝不调用 _generate
    class _ExplodingGateway:
        async def call(self, **kwargs):
            raise AssertionError("续跑命中时不应再调用模型 _generate")

    pkg2 = await _worker(pid, _ExplodingGateway()).execute_node(node, run_id="run-A")
    assert pkg2["node_status"] == "completed"
    assert pkg2.get("resumed") is True                # 明确标记为续跑
    # 续跑返回的产物引用与首次一致
    assert pkg2["output_code_refs"] == pkg1["output_code_refs"]
    assert pkg2["patch_refs"] == pkg1["patch_refs"]
    assert pkg2["artifacts"] == pkg1["artifacts"]


# ── 用例2：标记存在但产物文件缺失 → 不盲信，重新执行 ─────────────────────────

async def test_missing_artifact_forces_reexecution():
    pid = "proj-r172-resume-2"
    ws = _mk_ws(pid, {"source/a.cs": "int x=1;\n"})
    node = {"node_id": "n2", "node_type": "execution", "title": "t",
            "input_refs": ["source/a.cs"]}

    # 第一次执行：落产物 + done 标记
    gw1 = _CountingGateway(content="int x = 1; // migrated\n")
    pkg1 = await _worker(pid, gw1).execute_node(node, run_id="run-B")
    assert pkg1["node_status"] == "completed"
    assert len(gw1.calls) == 1
    out_ref = pkg1["output_code_refs"][0]
    marker = ws / "artifacts" / "p4_nodes" / "run-B" / "n2.done.json"
    assert marker.is_file()

    # 删除已落盘的 output_code 产物 → 标记引用的文件缺失
    (ws / out_ref).unlink()
    assert not (ws / out_ref).exists()

    # 第二次执行：标记虽在，但引用文件缺失 → 不信任 → 重新执行（_generate 被再次调用）
    gw2 = _CountingGateway(content="int x = 1; // migrated\n")
    pkg2 = await _worker(pid, gw2).execute_node(node, run_id="run-B")
    assert pkg2["node_status"] == "completed"
    assert len(gw2.calls) == 1, "标记引用文件缺失时必须重新执行（重新调用模型）"
    assert not pkg2.get("resumed"), "缺失产物不得被当作续跑命中"
    # 产物被重新落盘
    assert (ws / pkg2["output_code_refs"][0]).is_file()


# ── 用例3：产物被篡改（sha256 不符）→ 不盲信，重新执行 ───────────────────────

async def test_tampered_artifact_forces_reexecution():
    pid = "proj-r172-resume-3"
    ws = _mk_ws(pid, {"source/b.cs": "b\n"})
    node = {"node_id": "n3", "node_type": "execution", "title": "t3",
            "input_refs": ["source/b.cs"]}

    gw1 = _CountingGateway(content="orig content\n")
    pkg1 = await _worker(pid, gw1).execute_node(node, run_id="run-C")
    assert pkg1["node_status"] == "completed"
    out_ref = pkg1["output_code_refs"][0]

    # 篡改产物内容 → sha256/bytes 与标记记录不符
    (ws / out_ref).write_text("TAMPERED different content\n", encoding="utf-8")

    gw2 = _CountingGateway(content="orig content\n")
    pkg2 = await _worker(pid, gw2).execute_node(node, run_id="run-C")
    assert pkg2["node_status"] == "completed"
    assert len(gw2.calls) == 1, "校验不符（sha256 变化）时必须重新执行"
    assert not pkg2.get("resumed")


# ── 用例4：run_id 为空 → 不落标记（无法续跑，保持原行为）───────────────────

async def test_empty_run_id_skips_marker():
    pid = "proj-r172-resume-4"
    ws = _mk_ws(pid, {"source/c.cs": "c\n"})
    node = {"node_id": "n4", "node_type": "execution", "title": "t4",
            "input_refs": ["source/c.cs"]}
    pkg = await _worker(pid, _CountingGateway()).execute_node(node, run_id="")
    assert pkg["node_status"] == "completed"
    # 无 run_id → 不落 done 标记
    assert not (ws / "artifacts" / "p4_nodes").exists()
