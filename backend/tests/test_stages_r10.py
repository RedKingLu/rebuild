"""R10 T12 tests: P2/P3 stage API real (stage_plan persistence + real stage refs).

Proves get_stages / submit_stage_plan are REAL (persist to the stage_plan table,
source_status="real"), replacing the R4 mock. r_stage is R10 (routes_health).
"""

import pytest


@pytest.fixture
def project_run(client):
    pid = client.post("/api/projects", json={
        "name": "T12 Stage", "source_type": "manual",
    }).json()["data"]["project_id"]
    rid = client.post(f"/api/projects/{pid}/runs",
                      json={"run_goal": "t12", "mode": "plan"}).json()["data"]["run_id"]
    return pid, rid


def test_submit_stage_plan_persists_real(client, project_run):
    pid, rid = project_run
    r = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p2/stage-plan",
                    json={"plan_summary": "P2 评估计划", "plan_detail": {"scope": "risk"}})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["stage_plan_id"].startswith("sp-")   # real DB id
    assert data["source_status"] == "real" and data["transition_mode"] == "real"
    assert data["status"] == "under_review" and data["version"] == 1


def test_get_stages_reflects_real_stage_plan_ref(client, project_run):
    pid, rid = project_run
    sp_id = client.post(f"/api/projects/{pid}/runs/{rid}/stages/p2/stage-plan",
                        json={"plan_summary": "P2 计划"}).json()["data"]["stage_plan_id"]
    stages = client.get(f"/api/projects/{pid}/runs/{rid}/stages").json()["data"]["stages"]
    by_code = {s["stage_code"]: s for s in stages}
    assert by_code["P2"]["source_status"] == "real"       # no longer mock
    assert by_code["P2"]["stage_plan_ref"] == sp_id        # real ref from DB
    assert by_code["P3"]["stage_plan_ref"] is None         # no plan yet


def test_health_r_stage_is_r10(client):
    assert client.get("/api/health").json()["r_stage"] == "R10"
    assert client.get("/api/version").json()["r_stage"] == "R10"
