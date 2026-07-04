"""R11-3-C1 source 边界加固测试 — D-099 全局只读红线。

覆盖 R11-2 边界问题清单 B-1/B-2/B-4/B-5：
  - B-1/B-2: seed apply_patch_with_confirm 默认写 output_code/（非 source/）。
  - B-4: workspace 只读文案不再宣称"P4 开放"，改为"源码始终只读 + output_code/"。
  - B-5: WorkspaceMediator 对所有写主体（平台内部 + 外部）统一执行 source/ 只读、
         output_code/artifacts/patches 可写的边界（patches/ 平台可写见 D-104，
         用户 API 层仍只读）。

本环节不注册 P4 handler、不实现 P4 执行、不改路由逻辑。
"""

import pytest

from app.seed import RESOURCE_SEEDS
from app.services.workspace_mediator import WorkspaceMediator


# ── B-1/B-2: seed apply_patch_with_confirm 写盘目标 ──────────────────────

def _find_tool(name: str) -> dict:
    for entry in RESOURCE_SEEDS:
        if entry.get("name") == name:
            return entry
    raise AssertionError(f"seed tool {name!r} not found")


class TestC1SeedWriteScope:
    """B-1/B-2: apply_patch_with_confirm 默认写 output_code/，不再指向 source/。"""

    def test_apply_patch_write_scope_is_output_code(self):
        tool = _find_tool("apply_patch_with_confirm")
        assert tool["type_metadata"]["write_scope"] == "output_code"
        # 绝不再默认指向 source（D-099）
        assert tool["type_metadata"]["write_scope"] != "source"

    def test_apply_patch_still_requires_gate_L4(self):
        # 加固边界不得降低授权：仍为 L4 + requires_gate
        tool = _find_tool("apply_patch_with_confirm")
        assert tool["risk_level"].value == "L4"
        assert tool["type_metadata"]["requires_gate"] is True

    def test_apply_patch_description_targets_output_code(self):
        tool = _find_tool("apply_patch_with_confirm")
        desc = tool["description"]
        assert "output_code" in desc
        # 描述不再声称"应用补丁到源码"
        assert "应用补丁到源码" not in desc

    def test_generate_patch_still_patch_draft(self):
        # generate_patch 仍只产草案（不落盘），本环节不改
        tool = _find_tool("generate_patch")
        assert tool["type_metadata"]["write_scope"] == "patch_draft"


# ── B-5: WorkspaceMediator 全主体统一写盘边界 ────────────────────────────

class TestC1MediatorGlobalBoundary:
    """B-5: Mediator 对所有写主体（平台内部 worker + 外部平台）一视同仁。"""

    @pytest.fixture
    def ws(self, tmp_path):
        (tmp_path / "output_code").mkdir()
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "source").mkdir()
        (tmp_path / "patches").mkdir()
        return WorkspaceMediator(str(tmp_path))

    def test_write_source_rejected(self, ws, tmp_path):
        with pytest.raises(ValueError, match="read-only"):
            ws.check_write(str(tmp_path / "source" / "app.py"))

    def test_write_patches_allowed_L2(self, ws, tmp_path):
        # D-104: patches/ 平台可写（diff/patch 草案，L2），与 D-099③ 对齐。
        # 用户 API 层仍只读（workspace_service.READONLY_DIRS），此处校验的是平台写主体。
        target, risk = ws.check_write(str(tmp_path / "patches" / "change.diff"))
        assert str(target) == str((tmp_path / "patches" / "change.diff").resolve())
        assert risk == "L2"

    def test_write_output_code_allowed(self, ws, tmp_path):
        target, risk = ws.check_write(str(tmp_path / "output_code" / "new_file.txt"))
        assert str(target) == str((tmp_path / "output_code" / "new_file.txt").resolve())
        assert risk == "L1"

    def test_write_artifacts_allowed(self, ws, tmp_path):
        _, risk = ws.check_write(str(tmp_path / "artifacts" / "report.json"))
        assert risk == "L2"

    def test_write_executable_output_code_L3(self, ws, tmp_path):
        _, risk = ws.check_write(str(tmp_path / "output_code" / "migrate.py"))
        assert risk == "L3"

    def test_write_outside_allowed_dirs_rejected(self, ws, tmp_path):
        # 非 output_code/artifacts 的其它工作区目录也不可作为写盘落地目标
        with pytest.raises(ValueError, match="allowed write directories"):
            ws.check_write(str(tmp_path / "materials" / "x.txt"))

    def test_path_traversal_rejected(self, ws):
        with pytest.raises(ValueError, match="Boundary violation"):
            ws.check_write("/etc/shadow")

    def test_error_message_mentions_all_actors(self, ws, tmp_path):
        # 文案升格：不再限定"external platforms"
        with pytest.raises(ValueError) as ei:
            ws.check_write(str(tmp_path / "source" / "x.py"))
        assert "ALL actors" in str(ei.value)


# ── B-4: workspace 只读文案（API 级） ────────────────────────────────────

@pytest.fixture
def project_id(client):
    resp = client.post("/api/projects", json={
        "name": "R11-C1 Boundary Test", "source_type": "local_dir",
    })
    assert resp.status_code == 200
    return resp.json()["data"]["project_id"]


class TestC1WorkspaceReadonlyText:
    """B-4: 写 source/ 被拒，且文案不再宣称"P4 开放"。"""

    def test_source_write_denied_with_updated_text(self, client, project_id):
        w = client.put(f"/api/projects/{project_id}/file",
                       json={"path": "source/app.py", "content": "x=1"})
        assert w.status_code == 403
        body = w.text
        # 旧文案"执行阶段（P4）开放"必须消失
        assert "P4）开放" not in body
        # 新语义：只读 + output_code
        assert "只读" in body