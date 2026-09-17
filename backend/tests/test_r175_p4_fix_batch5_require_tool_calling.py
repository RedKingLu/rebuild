"""R17.5-P4-FIX 批5 — require_tool_calling 能力预检.

背景：批2 已把 P0-P3 (intake/profiling/assessment/planning) 与 tech_selection 改为
【工具循环 Node Worker Agent】(run_stage_tool_loop / call_stream + 工具)。这些阶段都
依赖模型支持 tool_calling。若用户选了不支持工具调用的模型，
应在上游 readiness 预检时【诚实 blocked + 明确提示】，而非运行时崩/空转。

本测试验证（全部 mock/真实注册表逻辑，禁跑 P0-P4 长链）：
  (a) 选支持 tool_calling 的模型 + require_tool_calling=True → readiness available。
  (b) 只有不支持 tool_calling 的模型 + require_tool_calling=True → readiness blocked +
      capability_unmet + 明确、尊重用户选择的 reason（不自动降级/改模型）。
  (c) 同一不支持工具的模型 require_tool_calling=False → available（证明是能力门禁在起作用，
      而非凭据/配置问题）。
  (d) 各工具循环阶段服务真调用 stage_model_readiness 时确实传了 require_tool_calling=True。

关联：AGENTS §2.3（无 Key/无能力不兜底，诚实 blocked）、D-097（失败不回退 mock）。
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# asyncio_mode=auto（pyproject）→ 无需模块级 mark。


@pytest.fixture(autouse=True)
def _isolate_credential_and_strategy_pollution():
    """隔离对【共享全局状态】的污染（同 test_r1736_wp6）：set_credential 写 os.environ 假 Key、
    update_strategy 覆盖共享 user_strategies.yaml。teardown 把凭据 env 确定性复位为 .env 真值，
    并还原策略/供应商共享文件，防止跨测试泄漏。"""
    from app.providers.provider_registry import _USER_STRATEGY_PATH
    _cred_vars = ("LLM_API_KEY", "DEEPSEEK_API_KEY", "MAAS_API_KEY", "OPENAI_API_KEY", "AGNES_API_KEY")
    _strategy_path = Path(_USER_STRATEGY_PATH)
    _strategy_before = _strategy_path.read_text(encoding="utf-8") if _strategy_path.exists() else None
    _providers_path = _strategy_path.parent / "user_providers.yaml"
    _providers_before = _providers_path.read_text(encoding="utf-8") if _providers_path.exists() else None
    _env_path = Path(__file__).resolve().parents[1] / ".env"
    try:
        yield
    finally:
        for v in _cred_vars:
            os.environ.pop(v, None)
        if _env_path.exists():
            for _line in _env_path.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _val = _line.partition("=")
                _k = _k.strip()
                if _k in _cred_vars and _k not in os.environ:
                    os.environ[_k] = _val.strip().strip('"').strip("'")
        for _p, _before in ((_strategy_path, _strategy_before), (_providers_path, _providers_before)):
            if _before is None:
                if _p.exists():
                    _p.unlink()
            else:
                _p.write_text(_before, encoding="utf-8")


def _fresh_registry_no_keys(monkeypatch):
    for var in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "MAAS_API_KEY", "OPENAI_API_KEY", "AGNES_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from app.providers.provider_registry import ProviderRegistry
    r = ProviderRegistry()
    r.load()
    return r


def _tool_calling_registry():
    """已配置且【支持 tool_calling】的模型（deepseek-v4-flash supports_tool_calling=true）。"""
    from app.providers.provider_registry import ProviderRegistry
    r = ProviderRegistry()
    r.load()
    r.set_credential("deepseek-official", "k-primary")
    r.update_strategy("system-default",
                      default_profile_ref="deepseek-official/deepseek-v4-flash",
                      fallback_profile_refs=[])
    return r


def _force_no_tool_calling(registry, profile_ref: str) -> None:
    """把指定 profile 在【本测试专属的 registry 实例】内显式覆盖为 supports_tool_calling=False。

    受控测试替身，不依赖真实配置里"某个模型恰好不支持工具调用"这一**易变事实**：
    原实现挑 minimax-m3（当时配置记为 false）作场景素材，本轮实测更正为 true 后
    该用例赖以成立的前提直接消失。改为显式构造能力缺失的候选，场景意图与配置解耦。
    用 dataclasses.replace 生成新对象（不原地改写），只作用于 fixture 内 new 出来的
    ProviderRegistry 实例，不触碰 model_profiles.yaml、不影响其他测试。
    """
    from dataclasses import replace
    original = registry.get_profile(profile_ref)
    assert original is not None, f"profile 不存在于配置：{profile_ref}"
    patched = replace(original, supports_tool_calling=False)
    registry._profiles[profile_ref] = patched
    provider = registry.get_provider(patched.provider_id)
    if provider is not None:
        provider.models = [patched if m.profile_id == profile_ref else m for m in provider.models]


def _non_tool_calling_registry():
    """已配置且【具凭据】但【不支持 tool_calling】的模型 → 凭据充足，唯独能力不满足。

    默认档与回退档均由 _force_no_tool_calling 显式覆盖为不支持工具调用（受控替身），
    不再依赖真实配置中这两个模型的 supports_tool_calling 取值。
    """
    from app.providers.provider_registry import ProviderRegistry
    r = ProviderRegistry()
    r.load()
    r.set_credential("maas-icompify", "k-notool")
    r.set_credential("agnes-ai", "k-notool2")
    r.update_strategy("system-default",
                      default_profile_ref="maas-icompify/minimax-m3",
                      fallback_profile_refs=["agnes-ai/agnes-2.0-flash"])
    # set_credential 先跑（把 profile.status 置 configured），再覆盖能力位。
    _force_no_tool_calling(r, "maas-icompify/minimax-m3")
    _force_no_tool_calling(r, "agnes-ai/agnes-2.0-flash")
    return r


# ═══════════════════════════════════════════════════════════════════════
# (a) 支持 tool_calling 的模型 + require_tool_calling=True → available
# ═══════════════════════════════════════════════════════════════════════

def test_readiness_tool_capable_model_available_with_require():
    from app.services.model_gateway import ModelGateway
    gw = ModelGateway(registry=_tool_calling_registry())
    r = gw.stage_model_readiness(strategy_id="system-default", require_tool_calling=True)
    assert r["available"] is True, r
    assert r["capability_ok"] is True
    assert any(c["outcome"] == "candidate_ready" for c in r["attempted_chain"])


# ═══════════════════════════════════════════════════════════════════════
# (b) 不支持 tool_calling 的模型 + require_tool_calling=True → blocked + 明确 reason
# ═══════════════════════════════════════════════════════════════════════

def test_readiness_non_tool_model_blocked_with_require():
    from app.services.model_gateway import ModelGateway
    gw = ModelGateway(registry=_non_tool_calling_registry())
    r = gw.stage_model_readiness(strategy_id="system-default", require_tool_calling=True)
    assert r["available"] is False, r
    assert r["capability_ok"] is False
    # 凭据充足 → 唯独能力不满足：链路条目为 capability_unmet（非 credential_missing）
    assert r["attempted_chain"], "应给出候选模型链路"
    assert all(c["outcome"] == "capability_unmet" for c in r["attempted_chain"]), r["attempted_chain"]
    # reason 明确点名 tool_calling、说明本阶段需工具调用、尊重用户选择不自动降级
    assert "tool_calling" in r["reason"]
    assert "工具" in r["reason"]
    assert "不会自动降级或替换" in r["reason"]
    # 每条候选的错误消息点名具体模型 + tool_calling
    for c in r["attempted_chain"]:
        assert "tool_calling" in c["error_message"]
        assert c["model"] and c["model"] in c["error_message"]
    # 用户可采取操作（切换模型策略）可透传前端
    assert any(a["action"] == "switch" for a in r["user_actions"])


# ═══════════════════════════════════════════════════════════════════════
# (c) 同一不支持工具的模型 require_tool_calling=False → available（证明是能力门禁在起作用）
# ═══════════════════════════════════════════════════════════════════════

def test_readiness_non_tool_model_available_without_require():
    from app.services.model_gateway import ModelGateway
    gw = ModelGateway(registry=_non_tool_calling_registry())
    r = gw.stage_model_readiness(strategy_id="system-default", require_tool_calling=False)
    assert r["available"] is True, r  # 凭据充足；不要求工具能力时可用


# ═══════════════════════════════════════════════════════════════════════
# (d) 各工具循环阶段服务真调用 readiness 时确实传了 require_tool_calling=True
#     用 spy 返回 blocked，使服务在预检即诚实中断（不触网、不跑工具循环长链）。
# ═══════════════════════════════════════════════════════════════════════

def _blocking_spy():
    return MagicMock(return_value={
        "available": False, "capability_ok": False,
        "reason": "所选模型不支持 tool_calling（工具调用）",
        "attempted_chain": [], "user_actions": [{"action": "switch", "label": "切换", "target": "models"}],
    })


async def test_intake_passes_require_tool_calling(monkeypatch):
    from app.services.model_gateway import ModelGateway
    from app.services.intake_service import IntakeService
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    spy = _blocking_spy()
    gw.stage_model_readiness = spy
    svc = IntakeService(gateway=gw)
    res = await svc.identify("proj-b5-p0", facts={})
    assert res.status == "blocked"
    assert spy.call_args.kwargs.get("require_tool_calling") is True


async def test_profiling_passes_require_tool_calling(monkeypatch):
    from app.services.model_gateway import ModelGateway
    from app.services.profiling_service import ProfilingService
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    spy = _blocking_spy()
    gw.stage_model_readiness = spy
    svc = ProfilingService(gateway=gw)
    res = await svc.profile("proj-b5-p1", facts={})
    assert res.status == "blocked"
    assert spy.call_args.kwargs.get("require_tool_calling") is True


async def test_assessment_passes_require_tool_calling(monkeypatch):
    from app.services.model_gateway import ModelGateway
    from app.services.assessment_service import AssessmentService
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    spy = _blocking_spy()
    gw.stage_model_readiness = spy
    svc = AssessmentService(gateway=gw)
    res = await svc.assess("proj-b5-p2", stage="p2")
    assert res.status == "blocked"
    assert spy.call_args.kwargs.get("require_tool_calling") is True


async def test_tech_selection_passes_require_tool_calling(monkeypatch):
    from app.services.model_gateway import ModelGateway
    from app.services.tech_selection_service import TechSelectionService
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    spy = _blocking_spy()
    gw.stage_model_readiness = spy
    svc = TechSelectionService(gateway=gw)
    res = await svc.select("proj-b5-tech", identification={})
    assert res.status == "blocked"
    assert spy.call_args.kwargs.get("require_tool_calling") is True


async def test_planning_stage_plan_passes_require_tool_calling(monkeypatch):
    from app.services.model_gateway import ModelGateway
    from app.services.planning_service import PlanningService
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    spy = _blocking_spy()
    gw.stage_model_readiness = spy
    svc = PlanningService(gateway=gw)
    res = await svc.generate_stage_plan("proj-b5-p3", user_goal="迁移到信创环境")
    assert res.status == "blocked"
    assert spy.call_args.kwargs.get("require_tool_calling") is True


async def test_planning_task_plans_passes_require_tool_calling(monkeypatch):
    from app.services.model_gateway import ModelGateway
    from app.services.planning_service import PlanningService
    gw = ModelGateway(registry=_fresh_registry_no_keys(monkeypatch))
    spy = _blocking_spy()
    gw.stage_model_readiness = spy
    svc = PlanningService(gateway=gw)
    res = await svc.generate_task_plans("proj-b5-p3", "sp-nonexistent", user_goal="迁移")
    assert res.status == "blocked"
    assert spy.call_args.kwargs.get("require_tool_calling") is True
