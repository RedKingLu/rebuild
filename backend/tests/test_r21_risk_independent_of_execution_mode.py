"""R21 补锁定测试：风险判定与执行模式无关（良性性质，此前无测试锁住）。

性质（读代码核实）：`execution_provider._classify_risk(code, language)` 与
`_check_dangerous(code)` 的签名只接受代码本身（外加语言），**不读执行模式**
（manual / plan / auto），也不读任何模式相关的全局/环境状态。语义上这是对的：

  - "这个动作本身有多危险" 只由动作决定 —— 同一条 `rm -rf /` 在三种模式下都必须是 L5；
  - 执行模式只影响 "是否还要额外问人"（见 `mode_policy.authorize_action`：manual 每步
    确认、plan 计划内低风险放行、auto 代理 L0-L3；L4+ 任何模式都不自动放行）。

如果哪天有人把模式塞进风险判定（例如"auto 模式下降级放行"），风险分级就会随模式漂移，
安检结论也不再可比。本文件从两个角度锁住这个性质：

  ① **结构性**：`_classify_risk` / `_check_dangerous` 的签名里不得出现 mode 形参；
     且判定结果不随任何模式相关的环境变量变化（不许从环境/全局偷读模式）。
  ② **行为性**：通过真正带执行模式的上层入口
     （`RemoteSSHExecutionProvider(..., mode=manual|plan|auto).execute()`，
     它内部先 `_check_dangerous` → `_classify_risk` → 再交 `authorize_action` 按模式裁决）
     断言同一条命令在三种模式下拿到**相同的 `risk_level`**，且 L5 的 block 结论、
     L4 的"须确认"结论三模式一致——模式只改变"还要不要问人"，不改变危险程度本身。

注意：本文件**不给 `_classify_risk` 加 mode 参数**（那是反向的）；样本沿用
`tests/test_r956_execution.py::TestClassifyRisk` 已有的用例，覆盖 L1/L3/L4/L5。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.execution_provider import (
    LocalSubprocessExecutionProvider,
    _check_dangerous,
    _classify_risk,
)
from app.services.mode_policy import authorize_action

MODES = ("manual", "plan", "auto")

# (code, language, expected_risk) —— 与 test_r956_execution.py::TestClassifyRisk 同源
RISK_SAMPLES: list[tuple[str, str, str]] = [
    ("echo hello", "bash", "L1"),
    ("ls -la /tmp", "bash", "L1"),
    ("pwd", "bash", "L1"),
    ("print('hello')", "python", "L1"),
    ("npm install", "bash", "L3"),
    ("make build", "bash", "L3"),
    ("import subprocess; subprocess.run(['ls'])", "python", "L3"),
    ("chmod 777 /srv/app/main.py", "bash", "L4"),
    ("systemctl stop nginx", "bash", "L4"),
    ("rm -rf /tmp", "bash", "L5"),
    ("sudo apt install", "bash", "L5"),
    (":(){ :|:&;};:", "bash", "L5"),
]

# 可能被"偷读"的模式相关环境变量（含本项目真实存在的 EXECUTION_MODE）
_MODE_ENV_KEYS = ("EXECUTION_MODE", "P5_EXECUTION_MODE", "MODE", "REBUILD_MODE",
                  "PROJECT_MODE")


# ══════════════════════════════════════════════════════════════════
# ① 结构性：签名里没有 mode，也不从环境偷读模式
# ══════════════════════════════════════════════════════════════════

class TestRiskFunctionsTakeNoMode:

    def test_classify_risk_signature_has_no_mode_param(self):
        params = list(inspect.signature(_classify_risk).parameters)
        assert params == ["code", "language"], (
            "_classify_risk 只应按 code+language 判定风险；新增 mode 形参会让"
            "风险等级随执行模式漂移（模式只该决定是否额外问人，见 mode_policy）")
        assert not any("mode" in p.lower() for p in params)

    def test_check_dangerous_signature_has_no_mode_param(self):
        params = list(inspect.signature(_check_dangerous).parameters)
        assert params == ["code"], (
            "_check_dangerous 是硬安检，只看命令本身；带上 mode 就意味着某些模式下"
            "可以放宽 DENY 列表")
        assert not any("mode" in p.lower() for p in params)

    @pytest.mark.parametrize("code,language,expected", RISK_SAMPLES)
    def test_classify_risk_ignores_mode_environment(self, monkeypatch, code, language,
                                                    expected):
        """把各种"模式"环境变量依次设成 manual/plan/auto：判定结果必须一字不变。"""
        baseline = _classify_risk(code, language)
        assert baseline == expected
        for key in _MODE_ENV_KEYS:
            for mode in MODES:
                monkeypatch.setenv(key, mode)
                assert _classify_risk(code, language) == baseline, (
                    f"{key}={mode} 改变了 {code!r} 的风险判定 → 风险分级偷读了执行模式")
            monkeypatch.delenv(key, raising=False)

    @pytest.mark.parametrize("code,language,expected", RISK_SAMPLES)
    def test_check_dangerous_verdict_ignores_mode_environment(self, monkeypatch, code,
                                                              language, expected):
        baseline = _check_dangerous(code)
        assert (baseline is not None) is (expected == "L5"), (
            "硬安检命中与 L5 判定必须一致（同一批 DENY 规则）")
        for key in _MODE_ENV_KEYS:
            for mode in MODES:
                monkeypatch.setenv(key, mode)
                assert _check_dangerous(code) == baseline, (
                    f"{key}={mode} 改变了 {code!r} 的安检结论")
            monkeypatch.delenv(key, raising=False)

    @pytest.mark.parametrize("code,language,expected", RISK_SAMPLES)
    def test_local_provider_risk_level_ignores_mode_environment(self, monkeypatch, code,
                                                               language, expected):
        """本地档 provider 的 execute() 返回的 risk_level 也不随模式环境变量变化。"""
        provider = LocalSubprocessExecutionProvider()
        for mode in MODES:
            monkeypatch.setenv("EXECUTION_MODE", mode)
            result = asyncio.run(provider.execute(code, language=language, timeout=5))
            assert result["risk_level"] == expected
        monkeypatch.delenv("EXECUTION_MODE", raising=False)


# ══════════════════════════════════════════════════════════════════
# ② 行为性：真正带执行模式的上层入口，三模式 risk_level 完全一致
# ══════════════════════════════════════════════════════════════════

_CS_PATH = "app.services.credential_service.CredentialService"


def _make_host():
    host = MagicMock()
    host.remote_host_id = "host-mode-independent"
    host.address = "192.168.1.100"
    host.masked_address = "192.168.***.100"
    host.port = 22
    host.credential_ref = "cred-123"
    host.host_key_fingerprint = None
    return host


def _execute_with_mode(code: str, language: str, mode: str) -> dict:
    """用 mode=manual|plan|auto 跑同一条命令。paramiko 全打桩，不出网。"""
    from app.services.remote_executor import RemoteSSHExecutionProvider

    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"stubbed"
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

    with patch(_CS_PATH) as MockCS, \
         patch("app.services.remote_executor._get_paramiko") as mock_pm:
        MockCS.return_value.decrypt.return_value = json.dumps(
            {"user": "deploy", "password": "s3cr3t"})
        mock_pm_module = MagicMock()
        mock_pm_module.SSHClient.return_value = mock_client
        mock_pm_module.RejectPolicy.return_value = MagicMock()
        mock_pm.return_value = mock_pm_module

        provider = RemoteSSHExecutionProvider(_make_host(), MagicMock(), mode=mode)
        return asyncio.run(provider.execute(code, language=language, timeout=5))


class TestRiskLevelIdenticalAcrossExecutionModes:

    @pytest.mark.parametrize("code,language,expected", RISK_SAMPLES)
    def test_risk_level_same_in_manual_plan_auto(self, code, language, expected):
        levels = {mode: _execute_with_mode(code, language, mode)["risk_level"]
                  for mode in MODES}
        assert set(levels.values()) == {expected}, (
            f"{code!r} 的 risk_level 随执行模式漂移了：{levels}")

    @pytest.mark.parametrize("code,language", [
        (c, l) for c, l, r in RISK_SAMPLES if r == "L5"])
    def test_l5_blocked_in_every_mode(self, code, language):
        """硬安检（L5）的 block 结论与模式无关——auto 不能把 DENY 命令放行。"""
        for mode in MODES:
            result = _execute_with_mode(code, language, mode)
            assert result["blocked"] is True, f"{code!r} 在 {mode} 模式下未被 block"
            assert result["risk_level"] == "L5"

    @pytest.mark.parametrize("code,language", [
        (c, l) for c, l, r in RISK_SAMPLES if r == "L4"])
    def test_l4_requires_confirmation_in_every_mode(self, code, language):
        """L4 在三种模式下都不自动放行（mode_policy 的高风险地板），结论一致。"""
        for mode in MODES:
            result = _execute_with_mode(code, language, mode)
            assert result["blocked"] is True, f"{code!r} 在 {mode} 模式下被自动放行了"
            assert result["risk_level"] == "L4"
            assert authorize_action(mode, "L4", action="remote_exec")["decision"] == \
                "require_confirmation"

    def test_mode_only_changes_whether_to_ask_not_the_risk(self):
        """模式的真实作用面：同一个 L3 风险，risk_level 不变，只有 decision 变。

        （manual 要确认 / plan 计划外要确认 / auto 代理放行——这正是"模式只影响是否
        还要额外问人"的证据，与风险判定解耦。）
        """
        code, language, expected = "npm install", "bash", "L3"
        risks = set()
        decisions = {}
        for mode in MODES:
            risks.add(_execute_with_mode(code, language, mode)["risk_level"])
            decisions[mode] = authorize_action(mode, expected, action="remote_exec")[
                "decision"]
        assert risks == {expected}
        assert decisions["manual"] == "require_confirmation"
        assert decisions["plan"] == "require_confirmation"
        assert decisions["auto"] == "auto_approved"
