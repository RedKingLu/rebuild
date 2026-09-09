"""R21 卫生批次：ExecutionProvider 能力接缝一致性断言（只补测试，不改产品代码）。

背景：`证据/进度追踪/01-功能表.md` 末尾的「能力接缝登记（R21）」用表格登记了四组接缝的
"接口声明方 / 实现提供方 / 使用消费方"。文档表格靠人工维护必然过期（`EXEC-06` 那行至今还写着
"三模式工厂"，而实际已有 5 个 provider 实现）。此处补一条**机器可判**的守卫，把最适合断言的
那一组（ExecutionProvider —— 工厂 mode 取值集合明确）钉住：

    工厂 `get_execution_provider()` 接线的 mode 分支集合  ⟷  代码里实际存在的 provider 实现类集合

任何一侧单方面变动都会让本文件失败：
  - 新增一个 provider 实现类但忘了在工厂里接线 ⇒ 实现类集合多出一项 ⇒ 失败；
  - 工厂里删/改了某个 mode 分支或改了它返回的类 ⇒ 两侧不一致 ⇒ 失败；
  - 两个 mode 指向同一个实现类（一对一关系被破坏）⇒ 失败。

刻意**不做**的事（D-065 NIH 约束 / 范围控制）：不解析那份 markdown 表格、不建生成器、不引任何
新依赖。两侧事实各自从代码里取：实现类侧用 `inspect` 反射，工厂侧用标准库 `ast` 读 `mode ==`
分支（不能靠调用工厂来枚举 —— `remote` 需要 db、`toolchain_container` 需要 provider_options）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.services import execution_provider as ep_mod
from app.services import remote_executor as re_mod

# provider 实现类分布的模块（接缝的"实现提供方"侧事实源）。
PROVIDER_MODULES = (ep_mod, re_mod)

# 工厂函数（接缝的"接口声明方"侧事实源）。
FACTORY_NAME = "get_execution_provider"


def _is_provider_impl(obj, module) -> bool:
    """结构判定：本模块内定义、有 `name` 字符串标识、有 async `execute` 的类。

    判定条件对齐 `ExecutionProvider` Protocol 的两个成员（`name` + `async execute`），
    不额外维护一份类名白名单（那又是一份会漂移的副本）。Protocol 本体自身排除。
    """
    if not inspect.isclass(obj):
        return False
    if obj.__module__ != module.__name__:
        return False
    if getattr(obj, "_is_protocol", False):
        return False
    if not isinstance(getattr(obj, "name", None), str):
        return False
    return inspect.iscoroutinefunction(getattr(obj, "execute", None))


def _impl_class_names() -> set[str]:
    found: set[str] = set()
    for module in PROVIDER_MODULES:
        for attr in vars(module).values():
            if _is_provider_impl(attr, module):
                found.add(attr.__name__)
    return found


def _returned_class_names(nodes) -> list[str]:
    """收集若干 AST 节点里所有 `return SomeClass(...)` 的被调用类名。"""
    names: list[str] = []
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call):
                func = sub.value.func
                if isinstance(func, ast.Name):
                    names.append(func.id)
                elif isinstance(func, ast.Attribute):
                    names.append(func.attr)
    return names


DEFAULT_MODE_SENTINEL = "<fallback>"


def _factory_mode_to_class() -> dict[str, str]:
    """从工厂源码解析 {mode 取值: 返回的实现类名}，兜底分支记为 DEFAULT_MODE_SENTINEL。"""
    tree = ast.parse(Path(ep_mod.__file__).read_text("utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == FACTORY_NAME), None)
    assert fn is not None, f"{FACTORY_NAME} 未找到 —— 工厂被改名/移动了，接缝登记需同步更新"

    mode_map: dict[str, str] = {}
    tail_returns: list[str] = []
    for stmt in fn.body:
        if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Compare) \
                and isinstance(stmt.test.left, ast.Name) \
                and stmt.test.left.id == "resolved_mode" \
                and len(stmt.test.comparators) == 1 \
                and isinstance(stmt.test.comparators[0], ast.Constant) \
                and isinstance(stmt.test.comparators[0].value, str):
            mode = stmt.test.comparators[0].value
            returned = _returned_class_names(stmt.body)
            assert len(returned) == 1, \
                f"mode='{mode}' 分支返回了 {returned}（期望恰好 1 个实现类）"
            mode_map[mode] = returned[0]
        elif isinstance(stmt, ast.Return):
            tail_returns.extend(_returned_class_names([stmt]))

    assert len(tail_returns) == 1, f"工厂兜底 return 期望恰好 1 个，实际 {tail_returns}"
    mode_map[DEFAULT_MODE_SENTINEL] = tail_returns[0]
    return mode_map


class TestExecutionProviderSeamConsistency:
    """EXEC 接缝：工厂 mode 集合 ⟷ provider 实现类集合，必须一对一且无遗漏。"""

    def test_every_impl_class_is_wired_into_factory(self):
        impls = _impl_class_names()
        wired = set(_factory_mode_to_class().values())
        missing = sorted(impls - wired)
        assert not missing, (
            f"这些 provider 实现类没有被 {FACTORY_NAME}() 接线，调用方永远拿不到它们："
            f"{missing}"
        )

    def test_factory_references_no_phantom_provider(self):
        impls = _impl_class_names()
        wired = set(_factory_mode_to_class().values())
        phantom = sorted(wired - impls)
        assert not phantom, (
            f"{FACTORY_NAME}() 引用的这些类不是（或已不再是）合格的 provider 实现"
            f"（需本模块内定义 + name 标识 + async execute）：{phantom}"
        )

    def test_mode_to_provider_is_one_to_one(self):
        mode_map = _factory_mode_to_class()
        impls = _impl_class_names()
        assert len(set(mode_map.values())) == len(mode_map), \
            f"mode 与实现类不再一对一（有类被多个 mode 复用）：{mode_map}"
        assert len(mode_map) == len(impls), \
            f"mode 数({len(mode_map)}) 与实现类数({len(impls)}) 不等：{mode_map} vs {sorted(impls)}"

    def test_seam_currently_has_five_providers(self):
        """现值锚点：5 个实现类 / 5 个 mode（含兜底）。

        这一条是刻意写死的**现值**快照：数目变化时它会失败，提示施工者去同步
        `证据/进度追踪/01-功能表.md` 的能力接缝登记与 EXEC-06 描述（旧描述"三模式工厂"
        就是这样悄悄过期的）。上面三条守恒断言才是结构守卫，本条只管"人去改文档"。
        """
        assert len(_impl_class_names()) == 5
        assert len(_factory_mode_to_class()) == 5

    @pytest.mark.parametrize("mode", ["workspace_local", "container"])
    def test_runtime_class_matches_wired_class_for_argless_modes(self, mode, monkeypatch):
        """无需外部参数即可构造的 mode：运行时真实返回的类 == AST 解析出的接线类。

        这一条把静态解析与真实运行行为对上，防止"AST 读对了但工厂运行时另有分支"。
        """
        monkeypatch.delenv("EXECUTION_MODE", raising=False)
        wired = _factory_mode_to_class()[mode]
        provider = ep_mod.get_execution_provider(mode=mode)
        assert type(provider).__name__ == wired

    def test_runtime_default_matches_wired_fallback(self, monkeypatch):
        """兜底分支即 "local" 档：不传 mode 与显式 mode="local" 都落到同一个实现类。"""
        monkeypatch.delenv("EXECUTION_MODE", raising=False)
        wired = _factory_mode_to_class()[DEFAULT_MODE_SENTINEL]
        assert type(ep_mod.get_execution_provider()).__name__ == wired
        assert type(ep_mod.get_execution_provider(mode="local")).__name__ == wired

    @pytest.mark.parametrize("mode", ["remote", "toolchain_container"])
    def test_wired_but_arg_dependent_modes_fail_loudly_without_inputs(self, mode, monkeypatch):
        """另两个 mode 需要外部输入（db / provider_options）⇒ 缺输入必须显式报错，不静默降级。

        与上面两条合起来，5 个 mode 全部有运行时覆盖：3 个验证返回类，2 个验证显式失败。
        """
        monkeypatch.delenv("EXECUTION_MODE", raising=False)
        with pytest.raises(ValueError):
            ep_mod.get_execution_provider(mode=mode)
