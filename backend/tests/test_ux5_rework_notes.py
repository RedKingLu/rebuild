"""UX-5 backend tests — rework-notes artifact write + read round-trip.

Covers: append_rework_notes appends to artifacts/{stage}_rework_notes.json, preserves
prior rounds, and the file is readable back through read_file (so the Acceptance agent's
rework conversation can surface it). Uses the temp test DB via conftest.
"""

import os
import sys
import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


def test_append_rework_notes_creates_then_appends(tmp_path, monkeypatch):
    from app.services import workspace_service as ws
    # Redirect workspace root to a temp dir so we don't touch real projects.
    monkeypatch.setattr(ws, "workspace_path", lambda pid: tmp_path / pid)

    notes_rel = ws.append_rework_notes("proj-x", "p4", "reject", "缺少数据库配置")
    assert notes_rel == "artifacts/p4_rework_notes.json"

    # The notes file is written under artifacts/ (platform-writable) and readable back.
    assert notes_rel.startswith("artifacts/")
    data = ws.read_file("proj-x", notes_rel)
    parsed = __import__("json").loads(data["content"])
    assert isinstance(parsed, list) and len(parsed) == 1
    assert parsed[0]["round"] == 1
    assert parsed[0]["decision"] == "reject"
    assert parsed[0]["reason"] == "缺少数据库配置"

    # Second rejection (request_changes) appends as round 2.
    ws.append_rework_notes("proj-x", "p4", "request_changes", "请补充安全审查")
    data2 = ws.read_file("proj-x", notes_rel)
    parsed2 = __import__("json").loads(data2["content"])
    assert len(parsed2) == 2
    assert [r["round"] for r in parsed2] == [1, 2]
    assert parsed2[1]["decision"] == "request_changes"
    assert parsed2[1]["reason"] == "请补充安全审查"
    # First round preserved.
    assert parsed2[0]["reason"] == "缺少数据库配置"


def test_append_rework_notes_writes_to_platform_dir(tmp_path, monkeypatch):
    """append_rework_notes writes to artifacts/ (platform-writable per D-099/D-104),
    never to the read-only source/ dir. The stage name is embedded in the filename, so
    even an unusual stage value can't redirect the write into source/."""
    from app.services import workspace_service as ws
    monkeypatch.setattr(ws, "workspace_path", lambda pid: tmp_path / pid)

    rel = ws.append_rework_notes("proj-x", "p4", "reject", "x")
    assert rel.startswith("artifacts/")
    assert "/source" not in rel
    # The write target must resolve inside the project workspace (boundary guard).
    target = ws.workspace_path("proj-x") / rel
    assert ws.workspace_path("proj-x").resolve() in target.resolve().parents \
        or ws.workspace_path("proj-x").resolve() == target.resolve()
