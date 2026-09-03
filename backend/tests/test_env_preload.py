"""Test app.main 的 .env 预加载器。

背景（新机迁移实测复现，2026-09-02）：main.py 顶部需在任何模块读取
os.environ 之前预加载 backend/.env。原实现虽然有
`if os.path.exists(_ENV_PATH):` 守卫，但紧接着无条件
`del _load_os, _ENV_PATH, _f, _line, _key, _val` —— 后四个名字只在守卫块内
绑定，因此以下三种情况都会抛 NameError 导致后端根本无法导入启动：
  ① 无 backend/.env（.gitignore 保护，外部贡献者 clone 后首跑必踩）
  ② .env 存在但为空
  ③ .env 存在但只有注释 / 无有效 KEY=VALUE 行
本测试锁定这三个回归点，并覆盖正常加载与 shell 优先级。
"""

import os

import pytest


@pytest.fixture()
def preload_env():
    """延迟导入 `app.main._preload_env`。

    与 `conftest.py` 及其余 12 个测试文件一致：**不在模块级导入 `app.main`**。
    conftest 明确记录过原因（B-DB-ISOLATION-1 / R11-3）——模块级导入会把
    `app.main` 提前到 collection 阶段载入，可能让服务层构造出指向【真实库】的
    engine 并被缓存，进而在 `drop_all` 时误删真实 `.data/rebuild.db`。
    """
    from app.main import _preload_env

    return _preload_env


def test_missing_env_file_is_noop(tmp_path, preload_env):
    """无 .env 时静默跳过，不抛异常（原 NameError 回归点 ①）。"""
    preload_env(str(tmp_path / "does-not-exist.env"))


def test_empty_env_file_is_noop(tmp_path, preload_env):
    """空 .env 时不抛异常（原 NameError 回归点 ②）。"""
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    preload_env(str(path))


def test_comment_only_env_file_is_noop(tmp_path, preload_env):
    """只有注释 / 空白行时不抛异常（原 NameError 回归点 ③）。"""
    path = tmp_path / ".env"
    path.write_text("# 只有注释\n\n   \n没有等号的行\n", encoding="utf-8")
    preload_env(str(path))


def test_loads_key_value_and_strips_quotes(tmp_path, monkeypatch, preload_env):
    """正常 KEY=VALUE 会进入 os.environ，且引号被剥离。"""
    path = tmp_path / ".env"
    path.write_text('REBUILD_TEST_PRELOAD="bar"\n', encoding="utf-8")
    # 先 set 再 del：让 monkeypatch 记录"原本不存在"，teardown 时清理掉
    monkeypatch.setenv("REBUILD_TEST_PRELOAD", "placeholder")
    monkeypatch.delenv("REBUILD_TEST_PRELOAD")

    preload_env(str(path))

    assert os.environ["REBUILD_TEST_PRELOAD"] == "bar"


def test_shell_env_takes_precedence(tmp_path, monkeypatch, preload_env):
    """已在 shell 中设置的变量不被 .env 覆盖（原实现语义，不得回退）。"""
    path = tmp_path / ".env"
    path.write_text("REBUILD_TEST_PRELOAD2=from_env_file\n", encoding="utf-8")
    monkeypatch.setenv("REBUILD_TEST_PRELOAD2", "from_shell")

    preload_env(str(path))

    assert os.environ["REBUILD_TEST_PRELOAD2"] == "from_shell"
