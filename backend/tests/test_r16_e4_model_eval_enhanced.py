"""R16-B E4: ModelEval enhanced routes — CSV import, agent_type filter, /compare."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import config as cfg
from app.core import database as db_mod
from app.dependencies import clear_services_cache


def _setup(monkeypatch, tmp_path):
    object.__setattr__(cfg.settings, "data_dir", str(tmp_path / "data"))
    object.__setattr__(cfg.settings, "source_dir", str(tmp_path / "source"))
    object.__setattr__(cfg.settings, "database_url", f"sqlite:///{tmp_path / 'rebuild.db'}")
    db_mod._engine = None
    db_mod._SessionLocal = None
    clear_services_cache()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    from app.main import app
    return TestClient(app)


def _seed_two_db_evaluations(db):
    from app.models.agent_model_eval import AgentModelEvalResult
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    rows = [
        AgentModelEvalResult(eval_id="ev-a", model_id="m1", agent_type="planner",
            task_type="migration_planning", scenario="Oracle→达梦", metric="rubric",
            score=4.3, success_rate=None, sample_count=30, source="导入数据",
            eval_method="rubric 1-5", limitations="样本30", published_at=now),
        AgentModelEvalResult(eval_id="ev-b", model_id="m2", agent_type="node_worker",
            task_type="migration_planning", scenario="Oracle→达梦", metric="pass_rate",
            score=0.82, success_rate=0.82, sample_count=50, source="社区贡献",
            eval_method="人工评审50样本", limitations="未覆盖触发器", published_at=now),
    ]
    for r in rows: db.add(r)
    db.commit()


def _client_with_db(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    from app.core.database import get_session
    db = get_session()
    try:
        _seed_two_db_evaluations(db)
    finally: db.close()
    from app.main import app
    return TestClient(app)


def test_list_filters_by_agent_type(client):
    db = _client_with_db
    # seed via direct DB then query
    from fastapi.testclient import TestClient as TC
    # simpler: use the client_with seeded evaluations
    c = _client_with_db(*_last_tmp()) if False else None


# Build a fresh seeded client inline
def _seeded_client(tmp_path, monkeypatch):
    return _client_with_db(tmp_path, monkeypatch)


def test_list_filters_by_agent_type(tmp_path, monkeypatch):
    c = _seeded_client(tmp_path, monkeypatch)
    r = c.get("/api/model-evaluations?agent_type=planner")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["evaluations"][0]["agent_type"] == "planner"


def test_list_matches_task_type(tmp_path, monkeypatch):
    c = _seeded_client(tmp_path, monkeypatch)
    r = c.get("/api/model-evaluations?task_type=migration_planning&scenario=Oracle%E2%86%92%E8%BE%BE%E6%A2%A6")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 2


def test_compare_two_models(tmp_path, monkeypatch):
    c = _seeded_client(tmp_path, monkeypatch)
    r = c.get("/api/model-evaluations/compare?task_type=migration_planning"
              "&scenario=Oracle%E2%86%92%E8%BE%BE%E6%A2%A6&model_ids=m1&model_ids=m2")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["task_type"] == "migration_planning"
    assert len(body["rows"]) == 2
    # ranked by score desc → m1 (4.3) before m2 (0.82) under default score sort
    assert body["rows"][0]["model_id"] == "m1"
    assert "不代表模型全局能力" in body["note"]


def test_import_csv_happy_path(tmp_path, monkeypatch):
    c = _seeded_client(tmp_path, monkeypatch)
    csv = "eval_id,model_id,task_type,scenario,metric,score,sample_count,source,eval_method,limitations,agent_type\n" \
          "ev-c,m3,code_migration,PL/SQL适配,pass_rate,0.90,40,社区导入,rubric,样本40,node_worker\n"
    r = c.post("/api/model-evaluations/import-csv",
               files={"file": ("evals.csv", csv.encode("utf-8"), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["imported"] == 1


def test_import_csv_rejects_non_csv(tmp_path, monkeypatch):
    c = _seeded_client(tmp_path, monkeypatch)
    r = c.post("/api/model-evaluations/import-csv",
               files={"file": ("evals.json", b"{}", "application/json")})
    assert r.status_code == 422


def test_import_csv_skips_missing_required(tmp_path, monkeypatch):
    c = _seeded_client(tmp_path, monkeypatch)
    csv = "eval_id,model_id,metric\n,missing-id,pass_rate\n"  # missing eval_id/model_id
    r = c.post("/api/model-evaluations/import-csv",
               files={"file": ("evals.csv", csv.encode("utf-8"), "text/csv")})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["imported"] == 0
    assert body["errors"] and len(body["errors"]) >= 1
