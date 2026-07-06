"""R13-6 ModelGateway is_fusion dispatch — integration tests.

Tests the gateway ROUTING DECISION (the actual R13-6 concern): does a `fusion/...`
user_override route to the FusionExecutionEngine?  We patch the DB lookup and the
dispatch so the test exercises the gateway wiring deterministically without a shared
DB session, mirroring how _resolve_key / _persist_call already use get_session().

Matrix (WP-6.1 ~ WP-6.5):
  - fusion/ prefix → dispatch to engine (ordinary registry bypassed)
  - unknown fusion ref → honest non-success (not fabricated)
  - non-fusion override → ordinary path untouched
  - call_stream with fusion/ prefix → streams synthesized content frames
  - return shape matches ordinary-model call + fusion_metadata
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.model_gateway import ModelGateway


class _FakeReg:
    """Registry stub: resolve_model returns a normal profile for non-fusion overrides."""

    def resolve_model(self, user_override=None, strategy_id="system-default", preferred_ref=None):
        if user_override and user_override.startswith("fusion/"):
            return None, "fusion_bypass", None
        # non-fusion path: return nothing configured → and the test asserts no engine dispatch
        return None, "not_configured", None

    def get_strategy(self, strategy_id):
        return None


class _FP:
    """Lightweight stand-in for FusionProfile (we patch the DB lookup)."""
    def __init__(self, vid="fusion/fp-test1"):
        self.fusion_profile_id = vid.split("/", 1)[1]
        self.virtual_profile_ref = vid
        self.name = "T"
        self.enabled = True


@pytest.fixture
def gw():
    return ModelGateway(registry=_FakeReg())


def _engine_ok(content="融合聚合成功"):
    return {
        "call_id": "fusion-fr-run1", "status": "completed", "content": content,
        "model": "fusion/fp-test1", "profile_id": "fusion/fp-test1", "provider_id": "fusion",
        "selection_reason": "fusion:panel_judge_synth", "latency_ms": 12.0,
        "error_category": "", "error_message": "",
        "usage_summary": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "retry_count": 0, "fallback_used": False,
        "fusion_metadata": {"strategy": "panel_judge_synth", "participants": ["p1/m", "p2/m"],
                          "judge_result": {"winner": "p1/m", "confidence": "high",
                                          "consensus": [], "contradictions": [],
                                          "partial_coverage": [], "unique_insights": [], "blind_spots": []}},
        "fusion_run_id": "fr-run1", "fusion_profile_id": "fp-test1",
    }


@pytest.mark.asyncio
async def test_fusion_override_dispatch(gw):
    fp = _FP("fusion/fp-test1")
    with patch.object(ModelGateway, "_load_fusion_profile", return_value=fp), \
         patch.object(ModelGateway, "_dispatch_fusion", new_callable=AsyncMock,
                      return_value=_engine_ok()):
        res = await gw.call(messages=[{"role": "user", "content": "hi"}],
                            user_override="fusion/fp-test1")
    assert res["status"] == "completed"
    assert res["content"] == "融合聚合成功"
    assert res["profile_id"] == "fusion/fp-test1"
    assert res["provider_id"] == "fusion"
    assert "fusion_metadata" in res


@pytest.mark.asyncio
async def test_fusion_override_not_found_is_honest(gw):
    """Unknown fusion virtual ref → honest non-success (R13-2C anti-fake). 必须是明确失败而非伪造成功。"""
    with patch.object(ModelGateway, "_load_fusion_profile", return_value=None):
        res = await gw.call(messages=[{"role": "user", "content": "hi"}],
                            user_override="fusion/fp-nonexistent")
    assert res["status"] != "completed"
    assert "fusion" in res.get("error_message", "").lower()


@pytest.mark.asyncio
async def test_ordinary_call_unchanged(gw):
    """Non-fusion override → ordinary path untouched, engine NOT dispatched."""
    dispatched = AsyncMock(side_effect=AssertionError("must not dispatch for ordinary override"))
    with patch.object(ModelGateway, "_dispatch_fusion", dispatched):
        res = await gw.call(messages=[{"role": "user", "content": "hi"}],
                            user_override="deepseek-official/deepseek-v4-flash")
    # registry stub returns configured profile; ordinary path reaches (mocked-fail)-free resolution
    dispatched.assert_not_called()


@pytest.mark.asyncio
async def test_call_stream_fusion_yields_tokens(gw):
    fp = _FP("fusion/fp-stream")
    with patch.object(ModelGateway, "_load_fusion_profile", return_value=fp), \
         patch.object(ModelGateway, "_dispatch_fusion", new_callable=AsyncMock,
                      return_value=_engine_ok(content="流式聚合OK")):
        frames = []
        async for frame in gw.call_stream(messages=[{"role": "user", "content": "hi"}],
                                          user_override="fusion/fp-stream"):
            frames.append(frame)
    types = [f["type"] for f in frames]
    assert types[-1] == "done", types
    assert all(t == "token" for t in types[:-1]), types
    content = "".join(f.get("content", "") for f in frames if f["type"] == "token")
    assert content == "流式聚合OK"


@pytest.mark.asyncio
async def test_return_shape_matches_ordinary_model(gw):
    """WP-6.5 等价性：is_fusion 结果包含普通模型调用的全部关键字段。"""
    fp = _FP("fusion/fp-shape")
    with patch.object(ModelGateway, "_load_fusion_profile", return_value=fp), \
         patch.object(ModelGateway, "_dispatch_fusion", new_callable=AsyncMock,
                      return_value=_engine_ok()):
        res = await gw.call(messages=[{"role": "user", "content": "hi"}],
                            user_override="fusion/fp-shape")
    for key in ["call_id", "status", "content", "model", "profile_id", "provider_id",
                "selection_reason", "latency_ms", "error_category", "usage_summary"]:
        assert key in res, f"missing ordinary-model field: {key}"
    assert "fusion_metadata" in res  # fusion-specific extra (上层按需取用)
