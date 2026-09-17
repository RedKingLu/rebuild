"""场景包发现与解析（R20-3）—— 发现 + IO + manifest 结构，不产任何 prompt 文本。

职责边界（③§7.1，三层不重叠）：
  scenario_loader.py    发现 + IO + manifest 结构（本模块）
  context_layers.py     把 manifest 渲染为层文本（所有 prompt 文案集中在那里）
  context_assembler.py  取 project.scenario → 调本模块 → 传给 layers（唯一装配入口 X-4-5）

设计硬约束（逐条对应一条判据，改动前请先读）：

1. **零硬编码场景值**（AGENTS §10-27）。场景值只用于两件事：①拼接目录路径（经本模块的形状
   校验 + 归属校验）；②原样放进 manifest 供上层插入 prompt 文本。本模块**不得**出现以场景值
   为键的查表，也**不得**出现以场景值为条件的 if / elif 分派分支。新增一个场景 = 新建一个目录，
   零 ``.py`` 改动。

2. **根目录必须与 skill_loader 同源**。``scenarios_root()`` 按 ``skill_loader.py:104-109``
   同样的方式解析：``SKILL_SOURCE_ROOT`` 环境变量优先，缺省派生自 ``settings.source_path``。
   原因见 ``backend/tests/conftest.py:158-167``：``source_dir`` 承担两种互相冲突的职责 ——
   ``source/cases|resources/`` 是测试【写】的目标（隔离到 tmp），而 ``source/skills/`` 是测试
   【真实读】的内容。conftest 把 ``source_dir`` 指向 tmp 做写隔离，再用 ``SKILL_SOURCE_ROOT``
   把读指回真实 skills 目录。若本模块另写一套根解析（例如直接用 ``settings.source_path``），
   测试期会落到不存在的 ``<tmp>/source/skills/scenarios``，所有场景测试全灭。

3. **不设任何缓存**（③§6.2）。``list_scenarios()`` 每次真扫目录、``resolve_scenario_pack()``
   每次真读文件。判据 R20-3-02 要求"用户改写场景包后，下次装配即读到改写后内容"；任何内容
   缓存都直接制造该判据的失败路径。单次装配只读 ≤4 个小文件，随后本就要发起一次 LLM 调用。

4. **不校验正文章节结构**。只要求 ``SKILL.md`` 存在且可读。四段结构（目标态词表 / 迁移与重构
   模式 / 常见陷阱与已知不兼容点 / 反例与禁止）是给内置包与用户的**建议模板**，不是加载条件 ——
   否则用户自建包会被"格式不符"卡住，违 R20-3-02（场景包须允许用户自由改写）。

5. **SKILL.md 解析原样复用 ``skill_loader.load_skill_body()``**，绝不写第二个解析器（DRY）。

6. **禁静默**（公理3 / AGENTS §10-21）。每个 except 都 (a) ``logger.warning`` 且
   (b) 把 ``(code, message)`` 二元组写进返回结构。禁止 ``except Exception: pass``。

7. **失败码是模块级字符串常量，刻意不用 ``enum.Enum``**。做成封闭 Enum 就变成一份封闭 code
   清单，与它所服务的那条决策（禁把场景做成固定枚举）自相矛盾。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from app.services.context_layers import (
    SCENARIO_ANCHORS_MAX_CHARS,
    SCENARIO_RISKS_MAX_CHARS,
    SCENARIO_SKILL_MAX_CHARS,
)
from app.services.skill_loader import load_skill_body

logger = logging.getLogger("rebuild.scenario_loader")

# ── 机制常量（目录名与文件名，不是场景值清单）────────────────────────────────
SCENARIOS_DIR_NAME = "scenarios"
GENERIC_SCENARIO_DIR = "_generic"   # 兜底包目录名。`_` 前缀 = 不可被用户选中，但可被解析
SKILL_FILENAME = "SKILL.md"
ANCHORS_FILENAME = "acceptance-anchors.md"
RISKS_FILENAME = "risk-catalog.md"
META_FILENAME = "meta.yaml"

TIER_TYPICAL = "typical"
TIER_OPEN = "open"

# ── 失败上报的稳定 code（lower-kebab-case 字符串常量，刻意不用 Enum，见模块 docstring 第 7 条）──
SC_ROOT_MISSING = "scenario-root-missing"
SC_ROOT_READ_ERROR = "scenario-root-read-error"
SC_SKILL_MISSING = "scenario-skill-missing"
SC_META_INVALID_YAML = "scenario-meta-invalid-yaml"
SC_META_INVALID_SHAPE = "scenario-meta-invalid-shape"
SC_META_UNKNOWN_KEYS = "scenario-meta-unknown-keys"
SC_FILE_READ_ERROR = "scenario-file-read-error"
SC_ID_INVALID = "scenario-id-invalid"
SC_PACK_NOT_FOUND = "scenario-pack-not-found"
SC_NOT_SELECTED = "scenario-not-selected"
SC_GENERIC_UNAVAILABLE = "scenario-generic-unavailable"
SC_ANCHORS_ABSENT = "scenario-anchors-absent"
SC_RISKS_ABSENT = "scenario-risks-absent"

# 场景 id 的【形状白名单】——约束字符集，**不约束取值集合**，故不与 AGENTS §10-27
# （禁把场景做成固定枚举/封闭清单）冲突。任何取值只要形状合法即可，取值集合由磁盘目录决定。
# 拒绝 "/"、"\"、".."、绝对路径、空串、前导 "_"（保留给内部包，如 _generic）与超长 id。
# ⚠️ 后续维护者请勿把这里"顺手"改成对具体场景值的枚举校验。
_SCENARIO_ID_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# meta.yaml 的已知【顶层键】清单（键名，不是场景值）。未知键不静默忽略，发 warning 并上报。
_META_KNOWN_KEYS = frozenset({
    "tier", "display_name", "summary",
    "selection_candidates", "related_skills", "related_resources",
})

# 厚度度量用的条目计数规则：Markdown 顶层/缩进列表项。抗灌水的主指标（候选数/风险数/锚点数）
# 都建立在它之上，故规则须单一、可复核。
_BULLET_RE = re.compile(r"^[ \t]*[-*+][ \t]+\S", re.MULTILINE)


def scenarios_root() -> Path:
    """场景包根目录。必须与 skill_loader 同源（见模块 docstring 第 2 条）。

    解析方式与 ``skill_loader.py:104-109`` 一致：
        Path(os.environ.get("SKILL_SOURCE_ROOT", str(settings.source_path / "skills"))) / "scenarios"
    测试期由 ``backend/tests/conftest.py:166-167`` 把 SKILL_SOURCE_ROOT 指回真实 skills 目录
    （读真实内容），而 settings.source_dir 指向 tmp（写隔离）。
    """
    from app.core.config import settings
    default_root = str(settings.source_path / "skills")
    return Path(os.environ.get("SKILL_SOURCE_ROOT", default_root)) / SCENARIOS_DIR_NAME


def list_scenarios() -> dict:
    """扫描场景包根，返回可选中场景的 manifest 列表 + 非法目录登记 + 发现状态。

    只列可选中场景：排除 `_` / `.` 前缀目录（`_generic` 是内部兜底包，不作为用户可选项）。
    每次真扫目录，无缓存。
    """
    root = scenarios_root()
    result: dict = {
        "scenarios": [],
        "invalid": [],
        "discovery_status": "ok",
        "root": str(root),
        "notices": [],
    }

    if not root.is_dir():
        msg = f"场景包根目录不存在或不是目录：{root}；平台将只能使用兜底场景包"
        logger.warning("scenario root missing: %s", root)
        result["discovery_status"] = "root_missing"
        result["notices"].append({"code": SC_ROOT_MISSING, "message": msg})
        return result

    try:
        children = sorted(p for p in root.iterdir())
    except OSError as e:
        msg = f"读取场景包根目录失败：{root}（{e}）"
        logger.warning("scenario root read error: %s: %s", root, e)
        result["discovery_status"] = "root_missing"
        result["notices"].append({"code": SC_ROOT_READ_ERROR, "message": msg})
        return result

    for child in children:
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("_") or name.startswith("."):
            continue  # 内部包 / 隐藏目录：不作为用户可选场景（`_generic` 仅经兜底路径解析）
        if not (child / SKILL_FILENAME).exists():
            msg = f"场景包目录 {name} 缺少 {SKILL_FILENAME}，不计入合法场景（目录未被修改或删除）"
            logger.warning("scenario pack invalid (no %s): %s", SKILL_FILENAME, child)
            result["invalid"].append({"dir": name, "code": SC_SKILL_MISSING, "message": msg})
            continue
        result["scenarios"].append(_read_pack(child, name))

    return result


def resolve_scenario_pack(scenario_id: Optional[str]) -> dict:
    """解析出用于注入的场景 manifest。

    id 为空 / 形状非法 / 归属校验失败 / 目录不存在 / 缺 SKILL.md → 回落 ``_generic``
    并携带 ``fallback=(code, message)``。**绝不回落到任何典型包**。
    ``_generic`` 自身不可用时 → 无场景文本，且明确上报 ``scenario-generic-unavailable``。
    """
    root = scenarios_root()
    raw = (scenario_id or "").strip()

    if not raw:
        return _fallback_pack(
            root, SC_NOT_SELECTED,
            "本项目尚未选择重构场景，已回落兜底场景包（无专用场景知识）；不得假设任何目标技术栈",
        )

    # 路径安全（按 R18-1 HIGH-01 同级对待）：形状白名单 → 归属校验 → is_dir。
    # 三步都直接写在本函数里：不走任何 Registry、不受任何 enabled 开关影响。
    # 依据：hook_engine._load_hooks 按 ResourceEntry.enabled == True 过滤，使安全拦截可被
    # 库里一行记录关掉 —— 新代码不得重复该模式。
    if not _SCENARIO_ID_SHAPE.match(raw):
        logger.warning("scenario id shape rejected (path safety): %r", raw)
        return _fallback_pack(
            root, SC_ID_INVALID,
            "所选场景标识不合法（仅允许字母数字与 _ . - 且不得以 _ 开头，长度 ≤64），已回落兜底场景包",
        )

    pack_dir = _safe_pack_dir(root, raw)
    if pack_dir is None:
        return _fallback_pack(
            root, SC_PACK_NOT_FOUND,
            f"场景包目录不存在或不可用：{raw}，已回落兜底场景包（无专用场景知识）",
        )
    if not (pack_dir / SKILL_FILENAME).exists():
        logger.warning("scenario pack has no %s: %s", SKILL_FILENAME, pack_dir)
        return _fallback_pack(
            root, SC_SKILL_MISSING,
            f"场景包 {raw} 缺少 {SKILL_FILENAME}，已回落兜底场景包（无专用场景知识）",
        )

    return _read_pack(pack_dir, raw)


# ── 内部实现 ─────────────────────────────────────────────────────────────────

def _safe_pack_dir(root: Path, dir_name: str) -> Optional[Path]:
    """归属校验 + is_dir。任何一步失败返回 None（调用方负责回落与发声）。

    归属校验用 resolve() + is_relative_to()，可拦住符号链接指向根外的绕过。
    本函数对内部包（`_generic`）与用户输入走**同一条**校验，无旁路。
    """
    try:
        root_resolved = root.resolve()
        candidate = (root / dir_name).resolve()
    except OSError as e:
        logger.warning("scenario path resolve failed: root=%s dir=%r: %s", root, dir_name, e)
        return None
    if not candidate.is_relative_to(root_resolved):
        logger.warning(
            "scenario path escapes root (rejected): %s not under %s", candidate, root_resolved)
        return None
    if not candidate.is_dir():
        return None
    return candidate


def _read_text(path: Path, max_chars: int, notices: list) -> tuple[str, bool, bool]:
    """读一份可选文本文件。返回 (text, truncated, present)。

    失败/缺失都不静默：缺失由调用方按文件语义上报；读失败在此 warning + 记 (code, message)。
    """
    if not path.exists():
        return "", False, False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("scenario file read error: %s: %s", path, e)
        notices.append({
            "code": SC_FILE_READ_ERROR,
            "message": f"场景包文件读取失败：{path.name}（{e}）；本节内容按缺失处理",
        })
        return "", False, False
    text = raw.strip()
    truncated = len(text) > max_chars
    return (text[:max_chars] if truncated else text), truncated, True


def _read_meta(path: Path, notices: list) -> dict:
    """读 meta.yaml。非法 YAML / 顶层非 mapping / 未知顶层键都发声但不使整包失效。"""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("scenario meta read error: %s: %s", path, e)
        notices.append({
            "code": SC_FILE_READ_ERROR,
            "message": f"场景包 {META_FILENAME} 读取失败（{e}）；元数据按缺失处理，tier 回落 {TIER_OPEN}",
        })
        return {}

    import yaml
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        logger.warning("scenario meta invalid yaml: %s: %s", path, e)
        notices.append({
            "code": SC_META_INVALID_YAML,
            "message": f"{META_FILENAME} 不是合法 YAML（{e}）；已忽略元数据，tier 回落 {TIER_OPEN}",
        })
        return {}

    if data is None:
        return {}
    if not isinstance(data, dict):
        logger.warning("scenario meta top-level not a mapping: %s (%s)", path, type(data).__name__)
        notices.append({
            "code": SC_META_INVALID_SHAPE,
            "message": f"{META_FILENAME} 顶层不是键值映射；已忽略元数据，tier 回落 {TIER_OPEN}",
        })
        return {}

    unknown = sorted(k for k in data if k not in _META_KNOWN_KEYS)
    if unknown:
        # 取 warning 级而非硬失败：场景包是用户可自由改写的资产，拼错一个键不应让整包失效。
        # 但绝不静默 —— 这正是"配置未知键必须发声"那条吸收项的核心。
        logger.warning("scenario meta unknown top-level keys in %s: %s", path, unknown)
        notices.append({
            "code": SC_META_UNKNOWN_KEYS,
            "message": f"{META_FILENAME} 含未识别的顶层键：{', '.join(unknown)}；已忽略，请核对拼写",
        })
    return data


def _as_list(value) -> list:
    """把 meta 字段规整为 list；标量视作单元素，其他类型视作空（不抛异常）。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, int, float, bool)):
        return [value]
    return []


def _read_pack(pack_dir: Path, scenario_id: str) -> dict:
    """读一个场景包目录，产出 manifest。每次真读盘，无缓存。"""
    notices: list = []

    # SKILL.md：原样复用 skill_loader（含 frontmatter 解析、not_connected 标注与截断标记）
    loaded = load_skill_body(str(pack_dir), max_chars=SCENARIO_SKILL_MAX_CHARS)
    capability_status = loaded.get("capability_status", "not_connected")
    if capability_status != "real":
        reason = loaded.get("not_connected_reason", "")
        logger.warning("scenario SKILL.md not loadable: %s (%s)", pack_dir, reason)
        notices.append({
            "code": SC_FILE_READ_ERROR,
            "message": f"场景包 {SKILL_FILENAME} 未能读取（{reason}）；场景知识正文缺失",
        })

    anchors_text, anchors_truncated, anchors_present = _read_text(
        pack_dir / ANCHORS_FILENAME, SCENARIO_ANCHORS_MAX_CHARS, notices)
    if not anchors_present:
        notices.append({
            "code": SC_ANCHORS_ABSENT,
            "message": f"本场景包未提供 {ANCHORS_FILENAME}（验收锚点）；验收须回落通用锚点",
        })
    risks_text, risks_truncated, risks_present = _read_text(
        pack_dir / RISKS_FILENAME, SCENARIO_RISKS_MAX_CHARS, notices)
    if not risks_present:
        notices.append({
            "code": SC_RISKS_ABSENT,
            "message": f"本场景包未提供 {RISKS_FILENAME}（风险清单）；风险研判须依真实源自行识别",
        })

    meta_path = pack_dir / META_FILENAME
    meta = _read_meta(meta_path, notices)

    # tier 是声明式元数据，不是特权：代码不因 tier==typical 授予任何额外能力，它只用于
    # 前端标注与厚度度量分组。缺失/非法/未知值一律按 open 处理，绝不上调为 typical。
    tier = TIER_TYPICAL if str(meta.get("tier", "")).strip() == TIER_TYPICAL else TIER_OPEN

    candidates = _as_list(meta.get("selection_candidates"))
    related_skills = [str(x) for x in _as_list(meta.get("related_skills"))]
    related_resources = [str(x) for x in _as_list(meta.get("related_resources"))]

    skill_body = loaded.get("body", "")
    files_present = {
        "skill": capability_status == "real",
        "anchors": anchors_present,
        "risks": risks_present,
        "meta": meta_path.exists(),
    }

    return {
        "scenario_id": scenario_id,
        "display_name": str(meta.get("display_name") or "").strip() or scenario_id,
        "tier": tier,
        "summary": str(meta.get("summary") or "").strip() or loaded.get("description", ""),
        "skill_body": skill_body,
        "skill_body_truncated": bool(loaded.get("body_truncated", False)),
        "anchors_text": anchors_text,
        "anchors_truncated": anchors_truncated,
        "risks_text": risks_text,
        "risks_truncated": risks_truncated,
        "files_present": files_present,
        "selection_candidates": candidates,
        "related_skills": related_skills,
        "related_resources": related_resources,
        "metrics": {
            "candidate_count": len(candidates),
            "risk_count": len(_BULLET_RE.findall(risks_text)),
            "anchor_count": len(_BULLET_RE.findall(anchors_text)),
            "resource_ref_count": len(related_skills) + len(related_resources),
            "skill_body_chars": len(skill_body),
            "files_present_all": all(files_present.values()),
        },
        "capability_status": capability_status,
        "fallback": None,
        "notices": notices,
        "directory": str(pack_dir),
    }


def _fallback_pack(root: Path, code: str, message: str) -> dict:
    """回落到兜底包。绝不代偿为任何典型包；兜底包自身不可用时诚实返回无场景文本。"""
    logger.warning("scenario fallback to %s: %s — %s", GENERIC_SCENARIO_DIR, code, message)
    generic_dir = _safe_pack_dir(root, GENERIC_SCENARIO_DIR)
    if generic_dir is None or not (generic_dir / SKILL_FILENAME).exists():
        unavailable = (
            f"兜底场景包 {GENERIC_SCENARIO_DIR} 不可用（根：{root}），本次装配无任何场景文本；"
            f"不得假设任何目标技术栈。触发原因：{code} —— {message}"
        )
        logger.warning("generic scenario pack unavailable under %s", root)
        return _empty_pack(SC_GENERIC_UNAVAILABLE, unavailable,
                           notices=[{"code": code, "message": message}])
    pack = _read_pack(generic_dir, GENERIC_SCENARIO_DIR)
    pack["fallback"] = {"code": code, "message": message}
    return pack


def _empty_pack(code: str, message: str, notices: Optional[list] = None) -> dict:
    """无任何场景文本的 manifest（诚实标注，不伪造场景知识）。"""
    files_present = {"skill": False, "anchors": False, "risks": False, "meta": False}
    return {
        "scenario_id": "",
        "display_name": "",
        "tier": TIER_OPEN,
        "summary": "",
        "skill_body": "",
        "skill_body_truncated": False,
        "anchors_text": "",
        "anchors_truncated": False,
        "risks_text": "",
        "risks_truncated": False,
        "files_present": files_present,
        "selection_candidates": [],
        "related_skills": [],
        "related_resources": [],
        "metrics": {
            "candidate_count": 0,
            "risk_count": 0,
            "anchor_count": 0,
            "resource_ref_count": 0,
            "skill_body_chars": 0,
            "files_present_all": False,
        },
        "capability_status": "not_connected",
        "fallback": {"code": code, "message": message},
        "notices": list(notices or []),
        "directory": "",
    }
