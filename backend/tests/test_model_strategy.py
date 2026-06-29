"""D-098 model strategy tests — single source of truth, no hardcoded model at call sites.

Verifies:
  - ModelGateway.resolve_call_target returns the user's strategy target (single source).
  - agent_loop no longer hardcodes a model/endpoint and resolves via the gateway (B-ORCH-02).
"""

import pathlib

from app.dependencies import get_services


def test_resolve_call_target_contract(client):
    """resolve_call_target returns a litellm-ready target from the configured strategy,
    or None when nothing is configured (caller surfaces an honest error)."""
    gw = get_services().model_gateway
    target = gw.resolve_call_target(strategy_id="system-default")
    if target is not None:
        for k in ("model", "api_base", "api_key", "provider_id", "profile_id"):
            assert k in target and target[k], f"missing {k} in resolve_call_target"
        # model is litellm-normalized (provider-prefixed)
        assert "/" in target["model"]


def test_agent_loop_has_no_hardcoded_model():
    """agent_loop must route model calls through gateway.call_stream(), not hardcode (D-098/B-ORCH-02/T5)."""
    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "agent_loop.py"
    text = src.read_text(encoding="utf-8")
    assert "call_stream" in text, "agent_loop must call gateway.call_stream() (T5)"
    assert "import litellm" not in text, "agent_loop must not import litellm directly (T5)"
    assert "litellm.acompletion" not in text, "agent_loop must not call litellm.acompletion directly (T5)"
    assert "openai/deepseek-chat" not in text, "agent_loop must not hardcode a model"
    assert "api.deepseek.com" not in text, "agent_loop must not hardcode an endpoint"


def test_opencode_has_no_hardcoded_model():
    """opencode adapter/server must resolve model/endpoint via the strategy (D-098/B-ORCH-02)."""
    base = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    adapter = (base / "opencode_adapter.py").read_text(encoding="utf-8")
    server = (base / "opencode_server.py").read_text(encoding="utf-8")
    # no hardcoded maas endpoint literal in either
    assert "maas.icompify.com" not in adapter, "opencode_adapter must not hardcode endpoint"
    assert "maas.icompify.com" not in server, "opencode_server must not hardcode endpoint"
    # both resolve via the shared strategy resolver
    assert "resolve_model_defaults" in adapter and "resolve_model_defaults" in server


def test_resolve_model_preferred_ref_soft_override(client):
    """preferred_ref (project global / agent custom) is a soft override: used if configured,
    else falls back to the strategy default (availability-driven, D-098)."""
    reg = get_services().model_gateway._registry
    # configured maas profile → preferred_ref honored
    _, reason, provider = reg.resolve_model(preferred_ref="maas-icompify/deepseek-v4-flash")
    if provider is not None:  # maas configured in this env
        assert reason == "preferred_ref"
        assert provider.provider_id == "maas-icompify"
    # bogus ref → falls through to strategy default (not a hard failure)
    profile2, reason2, _ = reg.resolve_model(preferred_ref="nonexistent/bogus-model")
    assert reason2 != "preferred_ref"


def test_project_global_mode_applies_global_model(client):
    """global_unified project applies its global_model_ref to call resolution (D-098)."""
    from app.services.project_service import ProjectService
    from app.core.database import get_session
    pid = client.post("/api/projects", json={
        "name": "Global Mode", "source_type": "manual", "source_config": {},
    }).json()["data"]["project_id"]
    db = get_session()
    try:
        ProjectService(db).update(pid, model_strategy_mode="global_unified",
                                  global_model_ref="maas-icompify/deepseek-v4-flash")
    finally:
        db.close()
    gw = get_services().model_gateway
    t = gw.resolve_call_target(strategy_id="system-default", project_id=pid)
    if t is not None:  # maas configured in this env
        assert t["model"] == "openai/deepseek-v4-flash"
        assert t["selection_reason"] == "preferred_ref"


def test_project_global_unavailable_falls_back(client):
    """global mode with a non-configured ref falls back to the system strategy (availability)."""
    from app.services.project_service import ProjectService
    from app.core.database import get_session
    pid = client.post("/api/projects", json={
        "name": "Global Fallback", "source_type": "manual", "source_config": {},
    }).json()["data"]["project_id"]
    db = get_session()
    try:
        ProjectService(db).update(pid, model_strategy_mode="global_unified",
                                  global_model_ref="nonexistent/bogus-model")
    finally:
        db.close()
    gw = get_services().model_gateway
    t = gw.resolve_call_target(strategy_id="system-default", project_id=pid)
    if t is not None:
        assert t["selection_reason"] != "preferred_ref"  # bogus ref ignored, fell back


def test_patch_project_model_strategy_persists(client):
    """PATCH /projects/{id} persists model_strategy_mode + global_model_ref; GET returns them (D-098 UI 后端)."""
    pid = client.post("/api/projects", json={
        "name": "PatchStrat", "source_type": "manual", "source_config": {},
    }).json()["data"]["project_id"]
    r = client.patch(f"/api/projects/{pid}", json={
        "model_strategy_mode": "custom", "global_model_ref": "maas-icompify/glm-5.2",
    })
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["model_strategy_mode"] == "custom"
    assert d["global_model_ref"] == "maas-icompify/glm-5.2"
    # GET reflects it
    g = client.get(f"/api/projects/{pid}").json()["data"]
    assert g["model_strategy_mode"] == "custom"


def test_onboarding_complete_persists_model_strategy(client):
    """onboarding/complete persists the model strategy chosen in the guide (D-086/D-098)."""
    pid = client.post("/api/projects", json={
        "name": "OnbStrat", "source_type": "manual", "source_config": {},
    }).json()["data"]["project_id"]
    r = client.post(f"/api/projects/{pid}/onboarding/complete", json={
        "execution_mode": "plan", "model_strategy_mode": "global_unified",
        "global_model_ref": "maas-icompify/glm-5.2",
    })
    assert r.status_code == 200, r.text
    g = client.get(f"/api/projects/{pid}").json()["data"]
    assert g["model_strategy_mode"] == "global_unified"
    assert g["global_model_ref"] == "maas-icompify/glm-5.2"



