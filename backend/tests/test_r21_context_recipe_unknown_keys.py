"""R21 卫生批次：`context_recipe` 未知键必须发声（公理3），且不得硬失败。

背景：`_resolve_recipe()` 用 `dict.update()` 把 Agent 定义的 `context_recipe` 整份合并进
`DEFAULT_CONTEXT_RECIPE`，但下游只逐个 `.get()` 少数已知键。写错键名（拼写错误 / 不存在的
配置项）此前**完全静默** —— 配置看起来生效了，实际一点作用都没有。

本文件钉住三件事：
  1. 含未知键 ⇒ 发 warning（写明 agent 与未识别键），且返回值行为不变（已知键仍生效、
     未知键仍原样合并）——只加一条警告，不改契约、不抛异常。
  2. 全是已知键 ⇒ 不发 warning（没有噪声）。
  3. agent=None / 无 context_recipe ⇒ 行为不变，也不发 warning。
并额外钉住"已知键集合派生自 DEFAULT_CONTEXT_RECIPE 单一事实源"（不是代码里另抄的清单）。
"""

from __future__ import annotations

import logging

import pytest

from app.services import context_assembler
from app.services.context_assembler import _resolve_recipe
from app.services.context_layers import DEFAULT_CONTEXT_RECIPE

LOGGER_NAME = "rebuild.context_assembler"


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records
            if r.name == LOGGER_NAME and r.levelno >= logging.WARNING]


class TestUnknownRecipeKeyWarns:
    def test_unknown_key_emits_warning_naming_agent_and_key(self, caplog):
        # Arrange：一个拼错的键（required_context_layer 少了 s）+ 一个不存在的配置项
        agent = {
            "agent_id": "agt-1",
            "name": "Node Worker Agent",
            "context_recipe": {
                "required_context_layers": ["C0", "C4"],
                "requred_context_layers": ["C9"],   # 拼写错误
                "max_tokens_per_layer": 123,        # 不存在的配置项
            },
        }

        # Act
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            recipe = _resolve_recipe(agent)

        # Assert：发声，且写明是哪个 agent / 哪些键 / 不会生效
        warns = _warnings(caplog)
        assert len(warns) == 1, f"应恰好发一条 warning，实际 {len(warns)}: {caplog.text}"
        msg = warns[0].getMessage()
        assert "Node Worker Agent" in msg and "agt-1" in msg
        assert "requred_context_layers" in msg and "max_tokens_per_layer" in msg
        assert "不会生效" in msg

        # Assert：返回行为一字不变 —— 已知键覆盖生效、未知键仍被合并、其余键取默认
        assert recipe["required_context_layers"] == ["C0", "C4"]
        assert recipe["requred_context_layers"] == ["C9"]
        assert recipe["max_tokens_per_layer"] == 123
        assert recipe["max_chars_per_skill"] == DEFAULT_CONTEXT_RECIPE["max_chars_per_skill"]

    def test_unknown_key_does_not_raise(self):
        """存量 DB 里可能已有带未知键的 Agent 定义 ⇒ 只能 warning，绝不能硬失败。"""
        agent = {"name": "legacy", "context_recipe": {"totally_bogus": {"a": 1}}}
        recipe = _resolve_recipe(agent)  # 不抛异常即为通过
        assert recipe["totally_bogus"] == {"a": 1}
        assert recipe["required_context_layers"] == DEFAULT_CONTEXT_RECIPE["required_context_layers"]

    def test_all_known_keys_emit_no_warning(self, caplog):
        agent = {
            "agent_id": "agt-2",
            "name": "Acceptance Agent",
            # 键全部取自单一事实源，杜绝本测试自身抄一份键名清单
            "context_recipe": {k: DEFAULT_CONTEXT_RECIPE[k] for k in DEFAULT_CONTEXT_RECIPE},
        }
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            recipe = _resolve_recipe(agent)
        assert _warnings(caplog) == []
        assert recipe == dict(DEFAULT_CONTEXT_RECIPE)

    def test_known_key_set_derives_from_default_recipe(self, caplog, monkeypatch):
        """已知键集合必须派生自 DEFAULT_CONTEXT_RECIPE：给默认配方加一个键，它立即算已知。

        若哪天有人在 `_resolve_recipe` 里手写一份键名清单（会漂移的副本），本例会失败。
        """
        patched = dict(DEFAULT_CONTEXT_RECIPE)
        patched["brand_new_layer_knob"] = 7
        monkeypatch.setattr(context_assembler, "DEFAULT_CONTEXT_RECIPE", patched)

        agent = {"name": "x", "context_recipe": {"brand_new_layer_knob": 9}}
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            recipe = _resolve_recipe(agent)
        assert _warnings(caplog) == []
        assert recipe["brand_new_layer_knob"] == 9


class TestNoRecipeUnchanged:
    @pytest.mark.parametrize("agent", [
        None,
        {},
        {"name": "no-recipe"},
        {"name": "empty-recipe", "context_recipe": {}},
        {"name": "null-recipe", "context_recipe": None},
    ])
    def test_missing_or_empty_recipe_returns_default_copy_silently(self, agent, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            recipe = _resolve_recipe(agent)
        assert _warnings(caplog) == []
        assert recipe == dict(DEFAULT_CONTEXT_RECIPE)
        # 必须是副本：改返回值不得污染模块级默认配方
        recipe["required_context_layers"] = ["C0"]
        assert DEFAULT_CONTEXT_RECIPE["required_context_layers"] != ["C0"]


class TestSeedAgentsCarryNoUnknownKeys:
    """存量事实钉住：内置 seed 的 5 个 Agent 定义都不该触发这条 warning（否则是真配置错误）。"""

    def test_seed_agent_recipes_are_all_known_keys(self, caplog):
        from app.seed import AGENT_SEEDS
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            for seed in AGENT_SEEDS:
                _resolve_recipe({"name": seed["name"], "context_recipe": seed.get("context_recipe")})
        assert _warnings(caplog) == [], f"seed Agent 配方含未知键：{caplog.text}"
