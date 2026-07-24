"""R17.5-P4-FIX 批4 (D-111): LLM 调用可观测性 + change_manifest 幽灵条目修复.

覆盖：
  1. CallLog 新列（内容 + 归因）经 _persist_call 落库、list_calls 读回；
  2. 内容脱敏（喂含 key 的 messages/completion → 落库 [REDACTED]，无明文）；
  3. list_calls 按 project_id / stage 归因过滤；
  4. 请求内容截断 + content_truncated 标记；
  5. change_manifest 只登记磁盘上真实存在的文件（幽灵引用 bytes=0 → 不出现）。

DB 隔离由 conftest.isolated_data 提供（每测试新建 SQLite + create_all）。
"""

import json

from app.services.model_gateway import (
    get_model_gateway, _redacted_messages, _redacted_completion, _attach_call_content,
)


def _base_record(call_id: str, **over) -> dict:
    rec = {
        "model_call_id": call_id, "provider_id": "prov", "profile_id": "prof",
        "strategy_id": "system-default", "selected_model": "m", "selection_reason": "r",
        "status": "completed", "latency_ms": 12.0, "error_category": "", "retry_count": 0,
        "fallback_used": False, "usage_summary": {"prompt_tokens": 1, "completion_tokens": 2,
                                                  "total_tokens": 3},
        "source": "api",
    }
    rec.update(over)
    return rec


# ── 1 + 2：内容 + 归因落库 & 脱敏 ────────────────────────────────────────────

def test_persist_writes_content_and_attribution_with_redaction():
    gw = get_model_gateway()
    rec = _base_record("call-d111-1")
    _attach_call_content(
        rec,
        messages=[{"role": "user", "content": "连到 db，api_key=sk-abcdefghijklmnopqrstuvwxyz012345"}],
        response="ok，token=sk-zyxwvutsrqponmlkjihgfedcba987654 完成",
        project_id="proj-A", run_id="run-A", stage="p4")
    gw._persist_call(rec)

    calls, total = gw.list_calls(limit=10, offset=0, project_id="proj-A")
    assert total == 1
    row = calls[0]
    assert row["project_id"] == "proj-A" and row["run_id"] == "run-A" and row["stage"] == "p4"
    # 内容已落库且脱敏（无明文 key，出现 [REDACTED]）
    assert "sk-abcdefghij" not in row["request_messages"]
    assert "sk-zyxwvutsrq" not in row["response_content"]
    assert "[REDACTED]" in row["request_messages"]
    assert "[REDACTED]" in row["response_content"]


def test_redacted_helpers_scrub_secrets():
    txt, _ = _redacted_messages([{"role": "user", "content": "password=Sup3rSecretValue123"}])
    assert "Sup3rSecretValue123" not in txt and "[REDACTED]" in txt
    resp, _ = _redacted_completion("here is api_key=sk-1234567890abcdefghijklmnop")
    assert "sk-1234567890" not in resp and "[REDACTED]" in resp


# ── 3：按 project_id / stage 过滤 ───────────────────────────────────────────

def test_list_calls_filters_by_project_and_stage():
    gw = get_model_gateway()
    for i, (pid, stage) in enumerate([("pX", "p0"), ("pX", "p4"), ("pY", "p4")]):
        rec = _base_record(f"c-{i}")
        _attach_call_content(rec, messages=[{"role": "user", "content": "hi"}],
                             response="ok", project_id=pid, run_id="r", stage=stage)
        gw._persist_call(rec)

    _, total_px = gw.list_calls(limit=50, project_id="pX")
    assert total_px == 2
    calls_px_p4, total_px_p4 = gw.list_calls(limit=50, project_id="pX", stage="p4")
    assert total_px_p4 == 1 and calls_px_p4[0]["stage"] == "p4"
    _, total_p4 = gw.list_calls(limit=50, stage="p4")
    assert total_p4 == 2
    # 不过滤 → 全部
    _, total_all = gw.list_calls(limit=50)
    assert total_all == 3


# ── 4：截断 + 标记 ─────────────────────────────────────────────────────────

def test_content_truncation_flag():
    big = "x" * 5000
    txt, truncated = _redacted_messages([{"role": "user", "content": big}])
    assert truncated is True and "[截断]" in txt
    resp, resp_trunc = _redacted_completion("y" * 20000)
    assert resp_trunc is True and "[截断]" in resp

    gw = get_model_gateway()
    rec = _base_record("c-trunc")
    _attach_call_content(rec, messages=[{"role": "user", "content": big}], response="short",
                         project_id="pT", run_id="r", stage="p4")
    gw._persist_call(rec)
    calls, _ = gw.list_calls(limit=5, project_id="pT")
    assert calls[0]["content_truncated"] is True


# ── 5：change_manifest 只登记磁盘真实存在的文件（幽灵引用剔除）───────────────

def test_change_manifest_excludes_ghost_references():
    from app.graph.stage_handlers import RealP4Handler
    from app.services import workspace_service

    pid = "proj-d111-ghost"
    ws = workspace_service.init_workspace(pid)
    real = ws / "output_code" / "real.py"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("print('migrated')\n", encoding="utf-8")

    handler = RealP4Handler(tracer=None, auditor=None, gateway=None)
    # 混入一个磁盘上不存在的幽灵 output_code_ref（模拟历史 AET 证据引用已删除文件）
    p4_evidence = [
        {"evidence_id": "ev-real", "output_code_ref": "output_code/real.py", "run_id": "r1"},
        {"evidence_id": "ev-ghost", "output_code_ref": "output_code/ghost_gone.py", "run_id": "r1"},
    ]

    class _Eng:
        completed_nodes = []
        failed_nodes = []
        gated_nodes = []
        node_results = {}
        graph_status = "completed"
        run_id = "r1"

    tg = {"task_graph_id": "tg-1", "nodes": [], "edges": []}
    ref = handler._write_execution_summary(pid, tg, [], _Eng(), p4_evidence, [], {}, [])
    assert ref is not None
    summ = json.loads((ws / ref).read_text(encoding="utf-8"))
    paths = [c["path"] for c in summ["change_manifest"]]
    assert "output_code/real.py" in paths, "真实落盘文件应在 change_manifest"
    assert "output_code/ghost_gone.py" not in paths, "磁盘不存在的幽灵引用不得进入 change_manifest"
    # 每条登记项 bytes 真实 > 0
    assert all(c["bytes"] > 0 and c["sha256"] for c in summ["change_manifest"])
