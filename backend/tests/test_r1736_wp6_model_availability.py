"""R17.3-6 WP-6 — 模型可用性策略化回退 + 全失败强制中断 + 前端显式报错 + Trace/Audit.

据用户裁决 Q-R17.3-6-2 与 r2/r3 施工基线。全部为真实（非 mock 生产码）测试：
  - 回退：真实 ModelGateway.call 顺位回退逻辑，仅在【外部 LLM SDK 边界】(litellm.acompletion)
    注入受控失败/成功（非 mock 生产回退逻辑）。
  - 全失败：真实"无凭据"路径（不触网、不 mock 生产码）→ 强制中断，验证无静默降级/假成功。
  - Trace/Audit：真实事件发射（注入记录型 writer 捕获）。
D-097 红线核心：失败不回退 mock、不规则兜底冒充 LLM。
"""

import os
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# asyncio_mode=auto（pyproject）→ 无需模块级 mark；async 测试自动异步执行，同步测试不受影响。


def _fresh_registry_no_keys(monkeypatch):
    """真实 ProviderRegistry，确保无任何可用凭据（删环境 Key，不设 credential）。"""
    for var in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "MAAS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from app.providers.provider_registry import ProviderRegistry
    r = ProviderRegistry()
    r.load()
    return r


def _two_configured_registry():
    """真实注册表 + 两个已配置模型（主 deepseek / 回退 maas glm），策略默认+回退链。"""
    from app.providers.provider_registry import ProviderRegistry
    r = ProviderRegistry()
    r.load()
    r.set_credential("deepseek-official", "k-primary")
    r.set_credential("maas-icompify", "k-fallback")
    r.update_strategy("system-default",
                      default_profile_ref="deepseek-official/deepseek-v4-flash",
                      fallback_profile_refs=["maas-icompify/glm-5.2"])
    return r


def _mock_llm_response(content="ok"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 3
    resp.usage.completion_tokens = 2
    resp.usage.total_tokens = 5
    return resp


# ═══════════════════════════════════════════════════════════════════════
# 1. 策略化顺位回退：主模型不可用 → 按 fallback 切到下一可用模型（真实回退逻辑）
# ═══════════════════════════════════════════════════════════════════════

async def test_strategy_fallback_switches_to_next_available(monkeypatch):
    """主 profile 调用失败（外部 SDK 注入失败）→ ModelGateway 真实按 fallback_profile_refs
    顺位切换到下一可用模型并成功。attempted_chain 记录 [主 failed, 回退 completed]。"""
    from app.services.model_gateway import ModelGateway

    gw = ModelGateway(registry=_two_configured_registry())

    async def _side_effect(*args, **kwargs):
        model = kwargs.get("model", "")
        if "deepseek" in model:  # 主模型：受控失败（外部 SDK 边界，非 mock 生产码）
            raise RuntimeError("provider down (injected)")
        return _mock_llm_response("fallback content")  # 回退模型：成功

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=_side_effect):
        result = await gw.call(messages=[{"role": "user", "content": "hi"}])

    assert result["status"] == "completed", result
    assert result["fallback_used"] is True
    assert result["content"] == "fallback content"
    assert result["profile_id"] == "maas-icompify/glm-5.2"
    chain = result["attempted_chain"]
    assert len(chain) == 2, chain
    assert chain[0]["outcome"] == "failed" and chain[0]["is_fallback"] is False
    assert chain[1]["outcome"] == "completed" and chain[1]["is_fallback"] is True


# ═══════════════════════════════════════════════════════════════════════
# 2. 全失败强制中断：所有模型不可用 → 中断 + attempted_chain + 无静默降级/假成功
# ═══════════════════════════════════════════════════════════════════════

async def test_all_models_unavailable_forces_interrupt_no_silent_degrade(monkeypatch):
    """无任一可用模型（无凭据，不触网）→ call() 强制中断：status 非 completed、
    model_unavailable=True、content 为空（无静默降级/无规则兜底冒充 LLM，D-097）、
    attempted_chain 记录所考察模型链路且无 completed。"""
    from app.services.model_gateway import ModelGateway
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))

    result = await gw.call(messages=[{"role": "user", "content": "hi"}])

    assert result["status"] != "completed"
    assert result["model_unavailable"] is True
    assert result["content"] == ""          # 无假成功
    assert result["error_category"] in ("model_unavailable", "credential_missing")
    chain = result["attempted_chain"]
    assert len(chain) >= 1
    assert all(c["outcome"] != "completed" for c in chain)  # 无任一模型成功


async def test_readiness_reports_unavailable_with_candidate_chain(monkeypatch):
    """stage_model_readiness：无凭据 → available=False + 候选模型链路 + 用户可采取操作
    （即便一次调用未发生，前端仍可显式呈现考察过的模型链路）。"""
    from app.services.model_gateway import ModelGateway
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))

    r = gw.stage_model_readiness(strategy_id="system-default")
    assert r["available"] is False
    assert r["attempted_chain"], "应给出候选模型链路"
    assert all(c["outcome"] in ("not_configured", "credential_missing", "capability_unmet")
               for c in r["attempted_chain"])
    assert any(a["action"] == "configure" for a in r["user_actions"])


async def test_readiness_available_when_configured():
    """有已配置且具凭据的模型 → available=True。"""
    from app.services.model_gateway import ModelGateway
    gw = ModelGateway(registry=_two_configured_registry())
    r = gw.stage_model_readiness(strategy_id="system-default")
    assert r["available"] is True


# ═══════════════════════════════════════════════════════════════════════
# 3. 结构化报错透传：阶段服务把 attempted_chain + 用户操作透传（前端显式报错所需）
# ═══════════════════════════════════════════════════════════════════════

async def test_assessment_service_propagates_model_unavailable(monkeypatch):
    """P2 评估：无可用模型 → blocked（不降级为规则评估），且携 attempted_chain +
    model_error_category + 用户可采取操作透传前端。"""
    from app.services.model_gateway import ModelGateway
    from app.services.assessment_service import AssessmentService
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    svc = AssessmentService(gateway=gw)

    res = await svc.assess("proj-wp6-p2", stage="p2")
    assert res.status == "blocked"
    assert res.model_error_category == "model_unavailable"
    assert res.attempted_chain, "应透传候选模型链路"
    assert any(a["action"] == "configure" for a in res.model_user_actions)


# ═══════════════════════════════════════════════════════════════════════
# 4. ValidationAgent：非完成且系模型中断 → 结构化 model_unavailable issue（供 work 节点建中断 Gate）
# ═══════════════════════════════════════════════════════════════════════

def test_validation_agent_emits_model_unavailable_issue(isolated_data):
    from app.services.validation_agent import ValidationAgent
    va = ValidationAgent("p2", "proj-wp6-va", run_id="r1")
    work_result = {
        "status": "blocked",
        "reason": "no_model_key: 评估需要 LLM 支持",
        "attempted_chain": [{"profile_id": "x/y", "provider_id": "x", "model": "y",
                             "outcome": "credential_missing", "error_category": "credential_missing",
                             "error_message": "无凭据", "is_fallback": False}],
        "model_error_category": "model_unavailable",
        "model_user_actions": [{"action": "configure", "label": "配置", "target": "models"}],
    }
    rr = va.validate(work_result)
    assert rr.passed is False
    mu = [i for i in rr.issues if isinstance(i, dict) and i.get("type") == "model_unavailable"]
    assert mu, f"应产出 model_unavailable issue，实际 issues={rr.issues}"
    detail = mu[0]["detail"]
    assert detail["attempted_chain"] and detail["error_category"] == "model_unavailable"


# ═══════════════════════════════════════════════════════════════════════
# 5. Trace/Audit：回退 + 全失败中断真实发射（注入记录型 writer 捕获，脱敏）
# ═══════════════════════════════════════════════════════════════════════

class _RecW:
    def __init__(self):
        self.events = []

    def write(self, *a, **kw):
        self.events.append({"args": a, "kw": kw})
        return {}


async def test_total_failure_emits_trace_and_audit(monkeypatch):
    """全失败中断 → 发射 model_unavailable Trace + Audit（含已尝试链路摘要，脱敏无 Key）。"""
    from app.services.model_gateway import ModelGateway
    tw, aw = _RecW(), _RecW()
    fake_services = types.SimpleNamespace(trace_writer=tw, audit_writer=aw)
    monkeypatch.setattr("app.dependencies.get_services", lambda *a, **k: fake_services)

    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    await gw.call(messages=[{"role": "user", "content": "hi"}], project_id="p", stage="p2")

    trace_actions = [e["kw"].get("action") for e in tw.events]
    audit_types = [e["kw"].get("audit_type") for e in aw.events]
    assert "model_unavailable" in trace_actions
    assert "model_unavailable" in audit_types
    # 脱敏：审计 reason 不含任何注入的 Key 值
    for e in aw.events:
        assert "k-primary" not in str(e) and "k-fallback" not in str(e)


async def test_fallback_emits_trace(monkeypatch):
    """成功回退 → 发射 model_fallback Trace（主→回退，可追溯）。"""
    from app.services.model_gateway import ModelGateway
    tw, aw = _RecW(), _RecW()
    fake_services = types.SimpleNamespace(trace_writer=tw, audit_writer=aw)
    monkeypatch.setattr("app.dependencies.get_services", lambda *a, **k: fake_services)

    gw = ModelGateway(registry=_two_configured_registry())

    async def _side_effect(*args, **kwargs):
        if "deepseek" in kwargs.get("model", ""):
            raise RuntimeError("down")
        return _mock_llm_response("ok")

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=_side_effect):
        await gw.call(messages=[{"role": "user", "content": "hi"}], project_id="p", stage="p2")

    assert "model_fallback" in [e["kw"].get("action") for e in tw.events]


# ═══════════════════════════════════════════════════════════════════════
# 6. 前后端契约：assessment-summary 端点在模型中断时透传 model_unavailable（D-074 联调）
# ═══════════════════════════════════════════════════════════════════════

def test_assessment_summary_surfaces_model_unavailable(client, isolated_data):
    """WorkAgent 落盘的 p2_model_error.json → assessment-summary 端点透传 model_unavailable
    结构（供 StagePageP2 ModelUnavailableBanner 显式报错）。真实端点，非 mock。"""
    import json
    from app.services.workspace_service import workspace_path
    from app.schemas.project import ProjectCreate
    svc = isolated_data
    proj = svc.project_service.create(ProjectCreate(name="wp6-summary", source_type="manual"))
    pid = proj.project_id if hasattr(proj, "project_id") else proj["project_id"]
    art = workspace_path(pid) / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_type": "model_error", "interrupted_stage": "p2",
        "failure_reason": "所有可用模型均不可用（credential_missing）",
        "error_category": "model_unavailable",
        "attempted_chain": [{"profile_id": "deepseek-official/deepseek-v4-flash",
                             "provider_id": "deepseek-official", "model": "openai/deepseek-v4-flash",
                             "is_fallback": False, "outcome": "credential_missing",
                             "error_category": "credential_missing", "error_message": "无凭据"}],
        "user_actions": [{"action": "configure", "label": "配置模型 / API Key", "target": "models"}],
    }
    (art / "p2_model_error.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    r = client.get(f"/api/projects/{pid}/assessment-summary")
    assert r.status_code == 200
    data = r.json()["data"]
    mu = data.get("model_unavailable")
    assert mu and mu["error_category"] == "model_unavailable"
    assert mu["attempted_chain"] and mu["attempted_chain"][0]["outcome"] == "credential_missing"
    assert any(a["action"] == "configure" for a in mu["user_actions"])

