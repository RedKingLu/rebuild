"""pytest fixtures — isolated data directory + DB per test (absorbed from V26.0 pattern)."""

import pytest
import tempfile
import os
import sys
import asyncio
import logging

from app.core.config import Settings
from app.dependencies import clear_services_cache, get_services
from app.core.database import _engine, _SessionLocal


# ══════════════════════════════════════════════════════════════════════════════
# B-ACC-G1-MOCKFLAG-FLAKY + B-RW-TESTPATH-FLAKY：运行前提机器化
# ══════════════════════════════════════════════════════════════════════════════
# 「运行前提未设导致假失败」在本项目已出现 4 次（PATH 缺失 2 次、误归因并发改动 1 次、
# R176_MOCK_LLM 缺失 1 次 → 2 个假失败 + `stage never reached p1 in 120s` 误导性报错）。
# 每次的教训都只让人记住【那一个变量】，没让人记住「运行前提是一个集合」，于是换个变量又犯。
# 结论：靠"提醒下一个执行者记得带某个变量"是无效治理，必须机器化。本节即该机器化。
#
# 两类前提按【是否改变"在测什么"】分治，不用同一种修法：
#   A 类 · 纯环境管道（补齐它不改变被测对象）        → 静默自愈
#   B 类 · 会改变被测对象（桩 vs 真实模型）          → 缺省安全值 + 强制声明，绝不静默
#
# 本节代码必须留在【模块级】而非 fixture 内：`test_r20_responses_channel.py:267` 之类的
# `skipif` 常量是在**收集期**（模块 import 时）读 os.environ 计算的，fixture 执行已太晚 ——
# 那会造成"skip 判定按真实模式算、fixture 却打了桩"的自相矛盾状态。
# conftest.py 先于同目录测试模块被 import，故此处赋值对所有收集期常量均可见。

_MOCK_LLM_ENV = "R176_MOCK_LLM"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def _selfheal_path() -> str | None:
    """A 类前提自愈：把【当前解释器所在 bin 目录】补进 PATH 首位。返回补入的目录或 None。

    为什么必须自愈：`test_r175_p5_r4_organic_green.py` 等用例**真实起子进程**跑
    `python3 -m compileall` / `python3 -m pytest`（`p5_command_service.py:160`）去验证
    P5 产出工程，而 `execution_provider.py:163` 明确继承宿主 PATH。若调用方没在命令前
    加 `PATH="$PWD/.venv/bin:$PATH"`，子进程解析到的是系统 `/usr/bin/python3`（实测该
    解释器 `No module named pytest`）→ 退出码 127/1 → 表现为**业务断言失败**，而真因是
    环境管道没接上。返工批次一与批次 H 都栽在这里，批次 H 还把它误归因为"其它 agent 并发改动"。

    为什么可以静默：补进来的目录就是**已经在跑本次 pytest 的那个解释器**自己的 bin
    （`sys.executable` 派生），不是外部猜测的路径。补齐后子进程看到的 python/pytest 与
    父进程完全一致 —— 这只是把父子进程的解释器视图对齐，**不改变"在测什么"**，
    因此不需要惊动任何人。同时它对【任何调用方式】都成立：带前缀时 venv bin 已在 PATH 中，
    本函数是幂等空操作；不带前缀时才补。

    只前置一个目录、不删不改任何既有条目，也不碰 PATH 之外的变量。
    """
    bin_dir = os.path.dirname(os.path.abspath(sys.executable))
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if bin_dir in parts:
        return None
    os.environ["PATH"] = os.pathsep.join([bin_dir, *parts])
    return bin_dir


def _resolve_mock_llm_mode() -> tuple[bool, str]:
    """B 类前提定档：决定本次运行用【桩】还是【真实模型】，返回 (是否用桩, 定档来源)。

    缺省用桩。理由：不加桩时涉及模型调用的用例会真打外网 —— 需有效 Key、产生费用、耗时受
    对端速率限制，且图驱动类用例会卡到 `_wait_for_stage` 超时后报出**与真因无关**的
    `stage never reached p1` —— 即"缺前提"伪装成"业务缺陷"。缺省用桩把这类假失败清零，
    也让 CONTRIBUTING 记录的基线数字成为**不带任何前缀就能复现**的默认结果。

    但缺省用桩绝不能静默：桩 vs 真实模型**改变了"在测什么"**。若默认开桩却不声明，会有人
    以为跑了真实模型模式而实际拿到桩 —— 这正是本项目最在意的诚实风险（No Evidence No
    Completed / 不得以 mock 冒充真实）。故配套 `pytest_report_header` +
    `pytest_terminal_summary` 两处横幅强制声明（见下）。

    显式 `R176_MOCK_LLM=0`（或 false/no/off）= 真实模型模式，覆盖缺省。

    无法识别的取值一律 fail-fast，不猜。原实现是 `!= "1"` 即真实模式，于是
    `R176_MOCK_LLM=true` 会**静默落到真实模型模式**并真花钱 —— 一个字面上表达"要桩"的
    取值产生了完全相反的效果。这类误解正是本节要消灭的对象，所以宁可拒绝启动。
    """
    raw = os.environ.get(_MOCK_LLM_ENV)
    if raw is None:
        os.environ[_MOCK_LLM_ENV] = "1"  # 落成真实 env，供子进程与收集期 skipif 一致可见
        return True, "缺省（未设置 R176_MOCK_LLM）"
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True, f"显式指定 R176_MOCK_LLM={raw}"
    if normalized in _FALSY:
        return False, f"显式指定 R176_MOCK_LLM={raw}"
    raise RuntimeError(
        f"{_MOCK_LLM_ENV} 取值无法识别：{raw!r}。"
        f"请用 {sorted(_TRUTHY)}（桩模式）或 {sorted(_FALSY)}（真实模型模式）之一，"
        f"或整个不设置（缺省为桩模式）。拒绝猜测：猜错会导致'以为跑了真实模型/以为打了桩'"
        f"的方向性误解，并可能产生真实费用。"
    )


_PATH_HEALED = _selfheal_path()
_MOCK_LLM, _MOCK_LLM_SOURCE = _resolve_mock_llm_mode()


# ══════════════════════════════════════════════════════════════════════════════
# B-ACC-BACKEND-PREREQ-SILENT-SKIP：C 类前提 · 后端可达性（探测，不自愈）
# ══════════════════════════════════════════════════════════════════════════════
# 批次零把 PATH（A 类）与 R176_MOCK_LLM（B 类）机器化了，但**这一条它治不了**——
# 测试进程起不了一个服务器。台账现象：全套件里唯一要求"后端在运行 + ≥2 provider 可达"的
# 真跑用例（`tests/test_r13_5_live_e2e.py`）在前提缺失时**诚实跳过**（行为正确、不是伪造），
# 但在 `-q` 下只表现为 **skip 计数 +1** ⇒ 读输出的人看不出少验了什么。与
# B-ACC-G1-MOCKFLAG-FLAKY 同族：**验证强度被静默削弱，且削弱不可见**。
#
# 本节按解除条件 ①②③ 落地，分两块：
#   ① 会话级**探测**（本节）：起始探一次后端可达性，写进两处横幅。
#      **探测而非自愈**（解除条件 ② 原话）：探到不可达就明确发声，**不试图去启动它**——
#      测试套件擅自拉起一个真实服务器会污染宿主端口与数据库，是比静默 skip 更坏的行为。
#   ② 结尾**逐条列举本次全部 skip 及其原因**（见 pytest_terminal_summary）：复用批次零
#      已建立的 `terminal_summary` 通道，**不另造机制**（解除条件 ① 明写）。
#      做成"列举全部 skip"而不是"只报后端那一条"，是因为家族教训已经很清楚：针对单个
#      变量/单条用例打补丁，换一个前提就再犯一次（该家族已出现 4 次）。
#
# 解除条件 ③（不得引入网络等待拖慢全量）：只做一次 TCP connect，超时
# `_BACKEND_PROBE_TIMEOUT_S` 秒；最坏代价即该常量本身，相对 40+ 分钟的全量可忽略。
# 刻意用 socket 而非 HTTP 请求：不需要知道后端答什么，只需要知道**有没有人在那个端口上**；
# TCP 层探测更快，且不会因某个路由 500 而误判为"后端没起"。
_BACKEND_PROBE_TIMEOUT_S = 0.3
# 端口标准单一事实源 = 后端统一 8000（AGENTS §10-19）。此处与
# `tests/test_r13_5_live_e2e.py` 的 `LIVE_BASE` 读**同一个环境变量**，不另立第二个开关。
_LIVE_BASE = os.environ.get("LIVE_BASE", "http://localhost:8000")


def _probe_backend_reachable(base: str) -> tuple[bool, str]:
    """TCP 层探测后端是否在监听。返回 (是否可达, 说明)。不抛异常、不启动任何进程。"""
    import socket
    from urllib.parse import urlsplit
    parts = urlsplit(base)
    host = parts.hostname or "localhost"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=_BACKEND_PROBE_TIMEOUT_S):
            return True, f"{host}:{port} 有进程在监听"
    except OSError as exc:
        return False, f"{host}:{port} 连接失败（{type(exc).__name__}）"


_BACKEND_REACHABLE, _BACKEND_PROBE_DETAIL = _probe_backend_reachable(_LIVE_BASE)


def _run_precondition_banner() -> list[str]:
    """本次运行的前提横幅。**任何人看一眼就知道自己跑的是桩还是真实模型。**"""
    if _MOCK_LLM:
        headline = "LLM 模式 = 桩 / MOCK —— 本次运行不发起任何真实模型调用"
        detail = ("litellm.acompletion / completion 被替换为即时返回的桩。"
                  "路由、图编排、状态机与持久化仍为真实执行。")
        switch = f"要跑真实模型模式：{_MOCK_LLM_ENV}=0 pytest ..."
    else:
        headline = "LLM 模式 = 真实模型 / REAL —— 本次运行会发起真实网络调用并可能产生费用"
        detail = ("模型调用未打桩，结果受 Key 有效性、对端速率限制与网络状况影响，不可离线复现。"
                  "这次运行的数字不得当作 CONTRIBUTING 的基线。")
        switch = f"要跑桩模式：{_MOCK_LLM_ENV}=1 pytest ...（或不设置该变量）"
    lines = [
        "═" * 78,
        f"  {headline}",
        f"  定档来源：{_MOCK_LLM_SOURCE}",
        f"  {detail}",
        f"  {switch}",
    ]
    if _PATH_HEALED:
        lines.append(f"  PATH 自愈：已前置解释器 bin 目录 {_PATH_HEALED}（子进程工具链可见性）")
    # B-ACC-BACKEND-PREREQ-SILENT-SKIP：后端可达性是**探测**结果，不自愈。
    if _BACKEND_REACHABLE:
        lines.append(f"  后端可达性：可达（{_BACKEND_PROBE_DETAIL}）—— 依赖真实后端的用例可真跑")
    else:
        lines.append(f"  后端可达性：【不可达】（{_BACKEND_PROBE_DETAIL}）—— 依赖真实后端的用例会"
                     f"诚实 skip，本次验证强度因此下降；套件不会替你启动后端（探测而非自愈）")
    lines.append("═" * 78)
    return lines


def _skip_reason(report) -> str:
    """从一份 skipped 报告里取出人类可读的跳过原因。取不到就如实说取不到。"""
    lr = getattr(report, "longrepr", None)
    if isinstance(lr, (tuple, list)) and len(lr) >= 3:
        return str(lr[2]).replace("Skipped: ", "", 1)
    return str(lr) if lr else "（未提供原因）"


def _skip_inventory_lines(terminalreporter) -> list[str]:
    """本次运行**每一条** skip 的逐条清单（B-ACC-BACKEND-PREREQ-SILENT-SKIP 解除条件 ①）。

    为什么必须逐条列出而不是只报后端那一条：本家族（"运行前提未设导致假失败/静默降级"）
    已出现 4 次，每次换一个变量或换一条用例就再犯一次。只治后端这一条，下一次换成
    "缺 docker" / "缺 dotnet SDK" 又会静默削弱一次。故把口径升级为
    **任何 skip 都必须在结尾可见**，`-q` 下也不例外（无需 `-rs`）。
    """
    reports = terminalreporter.stats.get("skipped", []) or []
    if not reports:
        return []
    lines = ["", f"本次跳过的用例（{len(reports)} 条，逐条列出原因 —— 跳过=少验了东西，不是通过）："]
    for r in reports:
        loc = getattr(r, "nodeid", None) or "（未知用例）"
        lines.append(f"  · {loc}")
        lines.append(f"      原因：{_skip_reason(r)}")
    if not _BACKEND_REACHABLE:
        lines.append("  提示：本次后端不可达，上列中依赖真实后端的用例属【前提缺失】而非代码问题；"
                     "补齐后端后重跑即可恢复该部分验证强度。")
    return lines


def pytest_report_header(config):
    """会话开头声明当前模式。"""
    return _run_precondition_banner()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """会话结尾**再**声明一次 —— 这一处比开头那处更重要，不是冗余。

    要防的误解是"有人读到 `1797 passed` 就以为那是真实模型跑出来的"，而那个数字出现在
    **末尾**。全量套件耗时 40-80 分钟、输出上千行，开头的横幅早已滚出屏幕；实际读日志的人
    读的是 `tail`。`pytest_terminal_summary` 的输出紧贴通过/失败计数上方，正好把"在测什么"
    与"测出了什么"钉在同一屏，任何 `tail -n 20` 都带得到。

    B-ACC-BACKEND-PREREQ-SILENT-SKIP 解除条件 ①：在同一处**追加本次全部 skip 的逐条清单**
    （复用本通道，不另造机制）——使 `-q` 下也能看出"少验了什么"，不必额外加 `-rs`。
    """
    for line in _run_precondition_banner():
        terminalreporter.write_line(line)
    for line in _skip_inventory_lines(terminalreporter):
        terminalreporter.write_line(line)


# B-R19-CAPLOG-ALEMBIC：本项目 logger 命名空间根。`rebuild.*` 是服务层 logger 前缀
# （logging.getLogger("rebuild.xxx")），`app.*` 是按模块名取的 logger（__name__）。
_PROJECT_LOGGER_ROOTS = ("rebuild", "app")


@pytest.fixture(autouse=True)
def _reenable_project_loggers():
    """B-R19-CAPLOG-ALEMBIC：每例前把本项目 logger 复位为「可发声」，让 caplog 断言稳定。

    根因：`alembic/env.py:14` 的 `fileConfig(config.config_file_name)` 默认
    `disable_existing_loggers=True`，会把**此前已导入**的 logger 全部置
    `disabled=True`。因此任一迁移相关用例先跑过之后，项目 logger 就被禁言，后面用
    `caplog` 断日志的用例抓不到任何 record —— 单跑通过、全量套件失败。
    而 `caplog.at_level()` / `set_level()` **只调整级别**，既不会把 `disabled=True`
    改回来，也不修 `propagate`，所以它救不了这个坑。

    同一个坑已咬两次（`B-R17.2-SSE-TEST-LEAK` → `test_r17_2_sse_events.py:95-100`
    就地打补丁；`test_r18_3_hookimpl_drift.py` 的 4 个 caplog 用例又打了一次），
    且触发条件（"某个迁移用例恰好先跑过"）对新测试作者完全不可见 —— 靠约定必然再犯，
    故在此统一根治。

    只复位「能不能发声」（`disabled` / `propagate`），**不设级别** —— 级别由各用例的
    `caplog.at_level()` 自行决定；此处强设会掩盖那些本该断言特定级别的用例。
    不改任何生产代码，不动 `alembic/env.py`（fileConfig 是 alembic 的正常用法）。
    """
    def _reset():
        for name, lg in list(logging.Logger.manager.loggerDict.items()):
            if not isinstance(lg, logging.Logger):
                continue  # PlaceHolder 无这些属性
            root = name.split(".", 1)[0]
            if root in _PROJECT_LOGGER_ROOTS:
                lg.disabled = False
                lg.propagate = True
        for root in _PROJECT_LOGGER_ROOTS:
            lg = logging.getLogger(root)
            lg.disabled = False
            lg.propagate = True

    _reset()
    yield
    # 用例过程中若又触发了 fileConfig（迁移类用例），下一例的 setup 会再复位；
    # 这里不做清理，避免把用例自己刻意设置的 logger 状态改掉。


@pytest.fixture(autouse=True)
def _maybe_mock_llm(request, monkeypatch):
    """在桩模式下把 litellm.acompletion 打成即时返回；真实模型模式下不介入。

    R17-6 makes graph resume run in the background; tests that assert on post-graph
    state need the graph to complete quickly. Real LLM calls can take 14s+ each due to
    provider timeouts. This fixture makes them instant so tests verify graph LOGIC
    (node routing, gate creation, stage advancement) without waiting on providers.

    模式判定不再在此处读 os.environ —— 已上移到模块级 `_resolve_mock_llm_mode()`
    （见文件头 B-ACC-G1-MOCKFLAG-FLAKY 一节）。原因有二：
      ① 收集期的 `skipif` 常量（如 `test_r20_responses_channel.py:267`）也要看同一个判定，
         若各自读 env 会出现"skip 按真实模式算、fixture 却打桩"的自相矛盾；
      ② 缺省值只应确定一次，并由横幅统一声明，避免第二事实源。
    桩模式为**缺省**：不带任何前缀跑 pytest 也是桩模式（含横幅声明）。
    """
    if not _MOCK_LLM:
        yield
        return

    class _MockChoice:
        def __init__(self, content="mocked llm response"):
            self.message = type("Msg", (), {"content": content, "tool_calls": None})()

    class _MockResponse:
        def __init__(self):
            self.choices = [_MockChoice()]
            self.usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()

    # 批2: services now route through call_stream (tool loop). When stream=True,
    # litellm.acompletion must return an async iterator of chunks — mirror that so the
    # streaming adapter yields a token then a done sentinel (no tool calls → single round).
    class _MockStreamChoice:
        def __init__(self, content):
            self.delta = type("D", (), {"content": content, "tool_calls": None})()

    class _MockStreamChunk:
        def __init__(self, content="", usage=None):
            self.choices = [_MockStreamChoice(content)] if content is not None else []
            self.usage = usage

    async def _fake_stream(content="mocked llm response"):
        yield _MockStreamChunk(content=content)
        yield _MockStreamChunk(content=None,
                               usage=type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                                    "total_tokens": 2})())

    async def _fake_acompletion(*args, **kwargs):
        if kwargs.get("stream"):
            return _fake_stream()
        await asyncio.sleep(0)  # yield once
        return _MockResponse()

    async def _fake_completion(*args, **kwargs):
        await asyncio.sleep(0)
        return _MockResponse()

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion)
    monkeypatch.setattr("litellm.completion", _fake_completion)
    yield


@pytest.fixture(autouse=True)
def isolated_data():
    """Isolate test data and database to a temporary directory per test.

    Pattern absorbed from V26.0 tests/conftest.py:
    tmp_path + monkeypatch + cache_clear for test isolation.
    """
    import shutil
    # Reset database globals so get_session picks up the new URL
    import app.core.database as db_mod
    db_mod._engine = None
    db_mod._SessionLocal = None

    tmp = tempfile.mkdtemp(prefix="rebuild-test-")
    db_url = f"sqlite:///{tmp}/rebuild.db"
    ws_tmp = os.path.join(tmp, "workspace")
    src_tmp = os.path.join(tmp, "source")
    settings = Settings(data_dir=tmp, debug=True, database_url=db_url, workspace_dir=ws_tmp,
                        source_dir=src_tmp)
    # workspace_service / trace_writer / audit_writer AND database.get_engine()
    # all read the GLOBAL settings singleton directly (not the injected one), so
    # every isolated field must be redirected on the global too — otherwise tests
    # pollute (and, for database_url, DROP) the real .data/rebuild.db + 工作区/ tree.
    # B-DB-ISOLATION-1 (R11-3): database_url was NOT overridden here, so
    # get_engine() kept pointing at the real DB and drop_all/create_all below
    # wiped the user's real projects on every pytest run. Redirect it too, and do
    # it BEFORE get_services() so no service can construct a real-DB engine that
    # then gets cached in db_mod._engine and drops the real DB at drop_all.
    # object.__setattr__ bypasses the frozen Settings.
    # REC-4 (R19-3): source_dir was NOT overridden here, so routes_upload /
    # routes_imports wrote skill/resource/case stubs into the REAL repo source/
    # tree on every pytest run (reproduced: source/cases/Test_Case_Import/,
    # source/resources/Imported_Security_Guide/). Same failure shape as
    # B-DB-ISOLATION-1 — redirect it on the global singleton too.
    import app.core.config as cfg
    _orig_ws = cfg.settings.workspace_dir
    _orig_db = cfg.settings.database_url
    _orig_data = cfg.settings.data_dir
    _orig_src = cfg.settings.source_dir
    object.__setattr__(cfg.settings, "workspace_dir", ws_tmp)
    object.__setattr__(cfg.settings, "database_url", db_url)
    object.__setattr__(cfg.settings, "data_dir", tmp)
    object.__setattr__(cfg.settings, "source_dir", src_tmp)
    # REC-4 后续修正：source_dir 承担了两种互相冲突的职责 —— `source/cases|resources/`
    # 是测试【写】的目标（必须隔离到 tmp），而 `source/skills/` 是测试【真实读】的内容。
    # 而 skill_loader.py:106-109 的 skill 根目录派生自 settings.source_path，一旦把
    # source_dir 整体指向 tmp，skill 读盘就落到不存在的 <tmp>/source/skills，
    # 导致 test_load_skills_for_stage_disk_fallback 与
    # TestP5SkillWiring::test_p5_skill_file_exists_and_has_frontmatter 失败（全量实测）。
    # 修法：用 skill_loader 本就留好的 SKILL_SOURCE_ROOT 覆盖口，把【读】指回真实 skills 目录，
    # 【写】仍隔离在 tmp。这样两种职责各归其位，不必在 Settings 上再切分字段。
    _orig_skill_root = os.environ.get("SKILL_SOURCE_ROOT")
    os.environ["SKILL_SOURCE_ROOT"] = os.path.join(_orig_src, "skills")
    clear_services_cache()
    svc = get_services(settings)
    # R9-3A: Drop + recreate tables to pick up new columns (SQLite create_all
    # won't alter existing tables, so old project table lacks coding_agent_ref).
    import app.models  # noqa: F401
    from app.models.base import Base
    from app.core.database import get_engine
    # B-DB-ISOLATION-1 safety guard: refuse to drop_all unless the engine is truly
    # bound to this test's temp DB. If a future change breaks the redirect above,
    # this raises loudly instead of silently wiping the real .data/rebuild.db.
    _eng = get_engine()
    if str(_eng.url) != db_url:
        raise RuntimeError(
            f"Test DB isolation broken: engine bound to {_eng.url!r}, expected {db_url!r}. "
            f"Refusing to drop_all on a non-temp database (would delete real data)."
        )
    Base.metadata.drop_all(bind=_eng)
    Base.metadata.create_all(bind=_eng)
    # R9-3E: Seed minimal test data so resource/agent/skill list tests pass
    _seed_test_data()
    # WP-6: bootstrap graph handlers so graph-based execute_onboarding works in tests
    try:
        from app.graph.stage_handlers import bootstrap_graph_handlers, _bootstrapped
        import app.graph.stage_handlers as _sh
        _sh._bootstrapped = False  # force re-bootstrap with test services
        bootstrap_graph_handlers(force=True)
    except Exception:
        pass  # graph bootstrap is best-effort in tests
    # Also reset the FlowRuntime singleton so it re-initializes with test DB
    try:
        from app.graph.runtime import reset_flow_runtime_for_test
        reset_flow_runtime_for_test()
    except Exception:
        pass
    # Reset checkpointer singleton so it re-opens with the test-isolated data_dir
    try:
        import asyncio as _aio
        from app.graph.checkpoint import close_checkpointer
        _loop = _aio.new_event_loop()
        _loop.run_until_complete(close_checkpointer())
        _loop.close()
    except Exception:
        pass
    yield svc
    # V-R17-1B-7: drain any outstanding background graph futures (R17-6 fire-and-forget
    # daemon threads from promotion-decision / gate decision) BEFORE restoring the global
    # settings.database_url. Otherwise a still-running daemon thread may lazily call
    # get_engine()/get_services() AFTER the redirect below is removed and cache a
    # real-DB engine in db_mod._engine, tripping a later test's isolation guard.
    # Production never drains; fire-and-forget semantics are unchanged there.
    try:
        from app.api.routes_stages import drain_graph_tasks
        drain_graph_tasks(timeout=30.0)
    except Exception:
        pass
    object.__setattr__(cfg.settings, "workspace_dir", _orig_ws)
    object.__setattr__(cfg.settings, "database_url", _orig_db)
    object.__setattr__(cfg.settings, "data_dir", _orig_data)
    object.__setattr__(cfg.settings, "source_dir", _orig_src)
    # 还原 SKILL_SOURCE_ROOT，避免泄漏到后续用例/进程
    if _orig_skill_root is None:
        os.environ.pop("SKILL_SOURCE_ROOT", None)
    else:
        os.environ["SKILL_SOURCE_ROOT"] = _orig_skill_root
    clear_services_cache()
    # Reset globals again so next test uses a fresh DB
    db_mod._engine = None
    db_mod._SessionLocal = None
    shutil.rmtree(tmp, ignore_errors=True)


def _seed_test_data():
    """Insert minimal test seed data for R6 model tests (resources, agents, skills).

    R9-3E: drop_all clears the persistent DB seed data. Tests expect
    these records to exist. We insert just enough to pass assertions.
    """
    from app.core.database import get_session
    from app.models.resource_entry import ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus
    from app.models.agent_definition import AgentDefinition, AgentType, AgentCategory, DefinitionStatus
    from app.models.skill_definition import SkillDefinition, SkillSeries, SkillCategory, SkillStatus
    import uuid

    db = get_session()
    try:
        # Seed 24+ resources covering multiple types
        rtypes = [ResourceType.agent, ResourceType.skill, ResourceType.tool,
                   ResourceType.hook, ResourceType.case, ResourceType.knowledge,
                   ResourceType.template, ResourceType.mcp]
        for i in range(30):
            rt = rtypes[i % len(rtypes)]
            # Case resources must be read_only (never executable)
            # Knowledge resources must be read_only/local_existing/externally_available
            if rt == ResourceType.case:
                st = ResourceStatus.read_only
            elif rt == ResourceType.knowledge:
                st = ResourceStatus.read_only
            else:
                st = ResourceStatus.active
            db.add(ResourceEntry(
                resource_id=str(uuid.uuid4()), name=f"Test Resource {i}",
                resource_type=rt, source_type=SourceType.user_provided,
                source_trust_level=TrustLevel.trusted_current, risk_level=RiskLevel.L0,
                status=st, description=f"Test {i}",
            ))
        # Seed agents of different types
        atypes = [AgentType.node_worker, AgentType.acceptance, AgentType.auto_review,
                   AgentType.expert, AgentType.conversation_gate]
        for i, at in enumerate(atypes):
            db.add(AgentDefinition(
                agent_id=str(uuid.uuid4()), agent_type=at, category=AgentCategory.system,
                name=f"Test {at.value}", status=DefinitionStatus.active,
                responsibilities=f"Test agent {i}", forbidden="",
            ))
        # Seed 24+ P-series skills
        scats = [SkillCategory.common, SkillCategory.p0, SkillCategory.p1, SkillCategory.p2,
                  SkillCategory.p3, SkillCategory.p4, SkillCategory.p5, SkillCategory.p6]
        for i in range(30):
            db.add(SkillDefinition(
                skill_id=str(uuid.uuid4()), name=f"Test P-Skill {i}",
                series=SkillSeries.P, category=scats[i % len(scats)],
                status=SkillStatus.active if i < 25 else SkillStatus.planned,
                description=f"Test skill {i}",
            ))
        # Seed a few R-series skills
        for i in range(3):
            db.add(SkillDefinition(
                skill_id=str(uuid.uuid4()), name=f"Test R-Skill {i}",
                series=SkillSeries.R, category=SkillCategory.other,
                status=SkillStatus.active, description=f"R skill {i}",
            ))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client(isolated_data):
    """FastAPI TestClient with isolated services."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
