"""Test health, version, meta endpoints."""


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["platform"] == "rebuild"
    assert data["version"] == "V26.1.1"
    # R17-2 V-R17-1B-1/P1-4：r_stage 改为动态读 settings.r_stage（config.py 默认 R17），
    # 不再硬编码 R10。测试只验证值在合法 R_STAGES 枚举内。
    from app.core.status import R_STAGES
    assert data["r_stage"] in R_STAGES


def test_version(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == "rebuild"
    assert data["version"] == "V26.1.1"


def test_meta(client):
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert "enums" in data
    assert "r_stages" in data["enums"]
    assert "p_stages" in data["enums"]
    # R9-5-1 阶段D: graph is real (LangGraph compiled) → source_status honest "real"
    assert data["source_status"] == "real"
    assert data["graph_status"]["graph_capability_status"] in ("live", "degraded")


def test_docs_available(client):
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_openapi_schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})
    # At minimum: /api/health, /api/version, /api/meta, /api/projects
    assert len(paths) >= 10, f"Expected >=10 paths, got {len(paths)}"
