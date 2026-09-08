"""R21 长时任务心跳注册表回归（B-R20-NO-LONGTASK-MONITOR）。

范围（红线，见交接说明）：只做"发现挂起并诚实标记"，不做自动重试/自动接管——本文件的
断言只覆盖：①心跳文件被真实写入且字段非占位；②查询函数三态判定（alive/stalled/unknown）
诚实、无数据不猜活；③阈值可配置；④P4 既有写入闭环行为不受影响（复用
test_r11_c4_p4_worker.py 的既有断言跑一遍，证明本次改动是纯旁挂）。
"""

from datetime import datetime, timedelta, timezone

from app.services import workspace_service
from app.services.aet_service import AETService
from app.services.heartbeat_service import (
    HEARTBEAT_REL_PATH,
    read_heartbeat_status,
    write_heartbeat,
)
from app.services.p4_execution_worker import P4ExecutionWorker


class _StubGateway:
    """确定性 ModelGateway：不依赖网络/Key，用于练习 execute_node 的真实写闭环。"""

    def __init__(self, content="// migrated to target stack\npublic class Migrated {}\n"):
        self.content = content

    async def call(self, **kwargs):
        return {"status": "completed", "content": self.content, "model_id": "stub-model"}


def _mk_ws(pid: str, sources: dict[str, str]):
    ws = workspace_service.init_workspace(pid)
    for rel, content in sources.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return ws


# ── 1. execute_node 真实写入心跳文件，字段真实非占位 ─────────────────────────

async def test_execute_node_writes_real_heartbeat_file():
    pid = "proj-hb-1"
    ws = _mk_ws(pid, {"source/Legacy.cs": "public class Legacy { }\n"})
    node = {"node_id": "n1", "node_type": "execution", "title": "迁移 Legacy.cs",
            "input_refs": ["source/Legacy.cs"]}
    before = datetime.now(timezone.utc)

    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    pkg = await worker.execute_node(node, run_id="hb-run-1")
    assert pkg["node_status"] == "completed"  # sanity: existing behaviour intact

    hb_file = ws / HEARTBEAT_REL_PATH
    assert hb_file.is_file()
    import json
    record = json.loads(hb_file.read_text(encoding="utf-8"))
    assert record["run_id"] == "hb-run-1"
    assert record["task_kind"] == "p4_execution"
    assert record["current_node"] == "n1"
    ts = datetime.fromisoformat(record["last_heartbeat_at"])
    # 真实时间戳，不是占位字符串：落在调用前后的合理窗口内
    assert before - timedelta(seconds=5) <= ts <= datetime.now(timezone.utc) + timedelta(seconds=5)


async def test_execute_node_without_run_id_skips_heartbeat():
    """无 run_id 时心跳没有可挂靠的 run（沿用 _write_node_marker 的既有 no-run_id 语义），
    不写心跳文件——不是本次施工要动的行为，只需确认不会崩/不会写出 run_id=None 的假记录。"""
    pid = "proj-hb-2"
    ws = _mk_ws(pid, {"source/x.cs": "x\n"})
    node = {"node_id": "n2", "node_type": "execution", "title": "t",
            "input_refs": ["source/x.cs"]}
    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    await worker.execute_node(node, run_id="")
    assert not (ws / HEARTBEAT_REL_PATH).exists()


async def test_run_loop_writes_heartbeat_per_node():
    """多节点 run() 循环：每处理完一个节点，心跳文件的 current_node 更新为最新节点。"""
    pid = "proj-hb-3"
    _mk_ws(pid, {"source/a.cs": "a\n", "source/b.cs": "b\n"})
    nodes = [
        {"node_id": "m1", "node_type": "execution", "title": "A", "input_refs": ["source/a.cs"]},
        {"node_id": "m2", "node_type": "execution", "title": "B", "input_refs": ["source/b.cs"]},
    ]
    worker = P4ExecutionWorker(pid, aet=AETService(None), gateway=_StubGateway())
    res = await worker.run(nodes, run_id="hb-run-3")
    assert res["status"] == "completed"  # sanity: existing run() behaviour intact

    status = read_heartbeat_status(pid, run_id="hb-run-3")
    assert status["status"] == "alive"
    assert status["heartbeat"]["current_node"] == "m2"  # 最后处理的节点


# ── 2. 查询函数三态：alive / stalled / unknown（诚实，无数据不猜活） ────────

def test_read_heartbeat_status_alive_within_threshold():
    pid = "proj-hb-4"
    workspace_service.init_workspace(pid)
    write_heartbeat(pid, "run-a", "p4_execution", current_node="n1")
    status = read_heartbeat_status(pid, run_id="run-a", stall_threshold_s=300)
    assert status["status"] == "alive"
    assert status["heartbeat"]["run_id"] == "run-a"
    assert status["age_seconds"] is not None and status["age_seconds"] >= 0


def test_read_heartbeat_status_stalled_when_old(tmp_path):
    """构造一个"心跳时间已经很久以前"的场景：直写一个过期时间戳的心跳文件，验证 stalled。"""
    pid = "proj-hb-5"
    ws = workspace_service.init_workspace(pid)
    hb_file = ws / HEARTBEAT_REL_PATH
    hb_file.parent.mkdir(parents=True, exist_ok=True)
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    import json
    hb_file.write_text(json.dumps({
        "schema": "heartbeat_v1", "run_id": "run-b", "task_kind": "p4_execution",
        "current_node": "n9", "last_heartbeat_at": stale_ts,
    }), encoding="utf-8")

    status = read_heartbeat_status(pid, run_id="run-b", stall_threshold_s=300)
    assert status["status"] == "stalled"
    assert status["age_seconds"] > 300


def test_read_heartbeat_status_unknown_when_no_file():
    """从未跑过心跳机制的项目（或旧版本产出、无心跳记录）→ unknown，不是 alive。"""
    pid = "proj-hb-6-never-heartbeated"
    workspace_service.init_workspace(pid)
    status = read_heartbeat_status(pid)
    assert status["status"] == "unknown"
    assert status["heartbeat"] is None


def test_read_heartbeat_status_unknown_when_run_id_mismatch():
    """心跳文件存在，但查询的是另一个 run_id → 对那个 run 而言没有数据，诚实 unknown。"""
    pid = "proj-hb-7"
    workspace_service.init_workspace(pid)
    write_heartbeat(pid, "run-current", "p4_execution", current_node="n1")
    status = read_heartbeat_status(pid, run_id="run-old-finished-run")
    assert status["status"] == "unknown"


# ── 3. 阈值可配置：同一份心跳数据，不同阈值 → 不同判定 ──────────────────────

def test_stall_threshold_is_configurable_not_hardcoded():
    pid = "proj-hb-8"
    workspace_service.init_workspace(pid)
    write_heartbeat(pid, "run-c", "p4_execution", current_node="n1")

    # 阈值给得很宽 → alive
    generous = read_heartbeat_status(pid, run_id="run-c", stall_threshold_s=3600)
    assert generous["status"] == "alive"

    # 阈值收得极窄（几乎任何真实耗时都会超过）→ stalled
    strict = read_heartbeat_status(pid, run_id="run-c", stall_threshold_s=0.0)
    assert strict["status"] == "stalled"

    assert generous["stall_threshold_s"] != strict["stall_threshold_s"]


def test_default_threshold_comes_from_settings_not_a_magic_number():
    """未传 stall_threshold_s 时，落到 app.core.config.settings 里可配置的字段，
    而不是函数体里散落的魔法数字。"""
    from app.core.config import settings
    pid = "proj-hb-9"
    workspace_service.init_workspace(pid)
    write_heartbeat(pid, "run-d", "p4_execution")
    status = read_heartbeat_status(pid, run_id="run-d")
    assert status["stall_threshold_s"] == settings.p4_heartbeat_stall_threshold_s


# ── 4. 只读查询入口（API）──────────────────────────────────────────────────

def test_heartbeat_api_endpoint_reports_status(client):
    r = client.post("/api/projects", json={"name": "hb-api-proj", "source_type": "manual",
                                            "source_config": {}})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["project_id"]

    # 尚未跑过任何长时任务 → unknown（不是 alive）
    r0 = client.get(f"/api/projects/{pid}/heartbeat")
    assert r0.status_code == 200, r0.text
    assert r0.json()["data"]["status"] == "unknown"

    write_heartbeat(pid, "api-run-1", "p4_execution", current_node="n1")
    r1 = client.get(f"/api/projects/{pid}/heartbeat", params={"run_id": "api-run-1"})
    assert r1.status_code == 200, r1.text
    body = r1.json()["data"]
    assert body["status"] == "alive"
    assert body["heartbeat"]["run_id"] == "api-run-1"


def test_heartbeat_api_endpoint_404_for_missing_project(client):
    r = client.get("/api/projects/does-not-exist/heartbeat")
    assert r.status_code == 404
