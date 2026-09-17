"""P5 源码/产出代码量对比 —— 非阻断【观察指标】(R22-4)。

要解决的缺口：P5 的硬必需槽位只验证 output_code/ 是否存在、patches 是否存在、P4
Evidence 是否真实（`p5_verification_service.py` 的 5 个硬必需槽位），**没有任何一条**看
"产出物相对源码的体量是否说得通"。一个只写了 3 个文件的迁移产出，与一个真迁了上千文件
的产出，在既有全部槽位上表现完全一致。本模块只补这一个此前完全缺失的观察维度。

四条不可让渡的边界（改本文件前必读。来源：`产物/草稿/R22-③完善方案.md` §1.1，
用户 2026-09-10 裁决"现在做成非阻断观察指标"）：
  ① **非阻断** —— 任何情况下不得使阶段 failed / blocked / rework。连本指标自身的度量
     与落盘失败也只 logger.warning + 继续（承 `p5_verification_service.
     _persist_build_log` 的范式：观察侧信道不得弄坏它所观察的对象）。
  ② 不进 Gate、不参与 P4→P5 晋级判定、不新增 Alembic 迁移、不加 API 端点。
  ③ **不设比例阈值** —— 本文件中不得出现任何硬编码比例常量。"多低算异常"须待真实规模
     样本（用户已排期后移）；凭猜定阈值就是把观察指标偷偷变成隐形硬 Gate。
  ④ 只给原始计数与比值 + 结构性异常标记，**不给**"产出物有无实质内容"的结论 —— 与
     `full_stack_profiler._ext_language_counts` 明确拒判 primary_language 的既有范式
     一致（AGENTS §2.3：确定性只做采集与验证，识别/研判/判定归 LLM）。

诚实三态口径（承 `heartbeat_service.read_heartbeat_status` 的 alive/stalled/unknown）：
度量本身失败 → `status="unknown"`，**绝不**默认 `"measured"`；`unknown` 时
`needs_manual_review` 亦为 True（度量不出来本身就该人看），且 `measurement_gaps` 里两个
missing 标记取 `None` 而不是 `False` —— 没看过就不能声称"不缺失"（公理 3 的同一精神）。

排除规则复用 `full_stack_profiler.SKIP_DIRS` 这一单一事实源，**禁止另抄一份副本**：
`B-R20-REDACT-THREE-IMPLS` 的教训是 `hook_engine` 抄了一份 `_SECRET_PATTERNS` 副本、
注释自称 mirror，源侧新增模式时副本没人跟随，导致一整类凭据在 pre-write 拦截上不设防。
排除集同理 —— 将来有人给源侧加一项，副本不会跟随，度量结果就会悄悄失真。
`backend/tests/test_r22_code_volume.py` 里有一条结构守卫断言钉住这件事。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 单一事实源复用（禁抄副本，见模块 docstring）。
from app.services.full_stack_profiler import SKIP_DIRS
from app.services.workspace_mediator import WorkspaceMediator
from app.services.workspace_service import workspace_path

logger = logging.getLogger(__name__)

# 落盘位置：对齐 heartbeat_service 的 `artifacts/p4/_heartbeat.json` 命名与位置范式，
# 写入经 WorkspaceMediator 单一写闸（D-099⑥），从不绕过。
CODE_VOLUME_REL_PATH = "artifacts/p5/_code_volume.json"

_SCHEMA = "code_volume_v1"

_STATUS_MEASURED = "measured"
_STATUS_UNKNOWN = "unknown"

# 度量两侧：源码侧为 D-099① 只读区（本模块只读不写），产出侧为 P4 真实写入目标。
SOURCE_REL_DIR = "source"
OUTPUT_REL_DIR = "output_code"

# 单文件读取上限（字节）。**不是比例阈值**：只防一个巨型文件把度量拖垮。超限文件只计
# 文件数、行数不计，并如实累加 measurement_gaps.binary_or_skipped_lines。
MAX_READ_BYTES = 2 * 1024 * 1024

# 行数计数的三种结局。
_COUNTED = "counted"
_UNREADABLE = "unreadable"
_NO_LINES = "no_lines"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_side() -> dict:
    return {"file_count": 0, "line_count": 0, "by_extension": {}}


def _decode(raw: bytes) -> Optional[str]:
    """按文本解码；解不出来返回 None（调用方计入 gaps，不静默丢弃）。

    BOM 处理照 `full_stack_profiler._read_text` 的既有口径（.NET/WebForms 源码常见
    UTF-16 BOM 文件，若一概判"二进制"会让行数口径对这类项目整体失真）。
    """
    try:
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16")
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig")
        return raw.decode("utf-8")
    except (UnicodeDecodeError, UnicodeError):
        return None


def _count_lines(path: Path) -> tuple[int, str]:
    """返回 (行数, 结局)。

    行数口径：`splitlines()` 计**文本行**，**不做**去注释/去空行的"有效代码行"识别
    （那需要逐语言注释语法，收益不明、复杂度高 —— KISS/YAGNI，且 ①②③ 三段一致）。
    """
    try:
        size = path.stat().st_size
    except Exception:
        logger.warning("code_volume: 取文件大小失败，如实计入 unreadable_files（不静默）：%s",
                       path, exc_info=True)  # 公理 3
        return 0, _UNREADABLE
    if size > MAX_READ_BYTES:
        logger.warning("code_volume: 文件超单文件读取上限 %d 字节，只计文件数不计行：%s",
                       MAX_READ_BYTES, path)
        return 0, _NO_LINES
    try:
        raw = path.read_bytes()
    except Exception:
        logger.warning("code_volume: 文件读取失败，如实计入 unreadable_files（不静默）：%s",
                       path, exc_info=True)  # 公理 3
        return 0, _UNREADABLE
    text = _decode(raw)
    if text is None:
        return 0, _NO_LINES
    return len(text.splitlines()), _COUNTED


def _measure_side(root: Path) -> tuple[dict, dict]:
    """扫一侧目录，返回 (原始计数, 该侧度量缺口)。

    排除检查对**相对路径的每一段**做（`any(p in SKIP_DIRS for p in parts)`），照
    `full_stack_profiler._scan` 的既有写法 —— 这样嵌套在深层的依赖/构建目录也能排除，
    而不是只看顶层。
    """
    side = _empty_side()
    gaps = {"unreadable_files": 0, "binary_or_skipped_lines": 0, "missing": False}
    if not root.is_dir():
        gaps["missing"] = True
        return side, gaps

    for f in sorted(root.rglob("*")):
        parts = f.relative_to(root).parts
        if any(p in SKIP_DIRS for p in parts):
            continue
        if not f.is_file():
            continue
        lines, outcome = _count_lines(f)
        bucket = side["by_extension"].setdefault(f.suffix.lower(), {"files": 0, "lines": 0})
        side["file_count"] += 1
        bucket["files"] += 1
        if outcome == _COUNTED:
            side["line_count"] += lines
            bucket["lines"] += lines
        elif outcome == _UNREADABLE:
            gaps["unreadable_files"] += 1
        else:
            gaps["binary_or_skipped_lines"] += 1

    side["by_extension"] = dict(sorted(side["by_extension"].items(),
                                       key=lambda kv: (-kv[1]["files"], kv[0])))
    return side, gaps


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    """分母为 0 → None。**不写 0、不写 inf** —— 那会被误读成"测出来是零/无穷"。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _unknown_record(reason: str) -> dict:
    """度量失败时的诚实记录：status=unknown，绝不默认 measured。

    两个 missing 标记取 None 而非 False —— 根本没扫成功，不能声称"不缺失"。
    """
    return {
        "schema": _SCHEMA,
        "measured_at": _now(),
        "source": _empty_side(),
        "output": _empty_side(),
        "ratio": {"by_file_count": None, "by_line_count": None},
        "needs_manual_review": True,
        "review_reasons": [reason],
        "measurement_gaps": {"unreadable_files": 0, "binary_or_skipped_lines": 0,
                             "source_missing": None, "output_missing": None},
        "status": _STATUS_UNKNOWN,
    }


def _review_reasons(output: dict, output_missing: bool) -> list[str]:
    """本轮只落**与阈值无关的结构性异常**（边界③：无真实规模样本，比例判断不落）。

    字段结构预留 ratio 供将来定阈值时直接用，但本轮代码里不出现任何比例常量。
    """
    if output_missing:
        return ["output_missing: 产出目录不存在，产出侧实际为零"]
    if output["file_count"] == 0:
        return ["output_empty: 产出目录存在但有效文件数为 0（已按 SKIP_DIRS 排除）"]
    if output["line_count"] == 0:
        return ["output_all_lines_zero: 产出侧有文件但总行数为 0（可能全是空文件）"]
    return []


def measure_code_volume(project_id: str) -> dict:
    """度量一次并返回观察对象（不落盘）。度量本身失败 → status="unknown"。

    只读两侧目录，不写任何东西 —— 源码侧是 D-099① 只读区。
    """
    try:
        ws = workspace_path(project_id)
        source, src_gaps = _measure_side(ws / SOURCE_REL_DIR)
        output, out_gaps = _measure_side(ws / OUTPUT_REL_DIR)
    except Exception:
        logger.warning("code_volume: 度量失败，诚实报 unknown（非阻断）：project=%s",
                       project_id, exc_info=True)  # 公理 3
        return _unknown_record("measurement_failed: 度量过程异常，未取得可信计数")

    reasons = _review_reasons(output, out_gaps["missing"])
    return {
        "schema": _SCHEMA,
        "measured_at": _now(),
        "source": source,
        "output": output,
        "ratio": {
            "by_file_count": _ratio(output["file_count"], source["file_count"]),
            "by_line_count": _ratio(output["line_count"], source["line_count"]),
        },
        "needs_manual_review": bool(reasons),
        "review_reasons": reasons,
        "measurement_gaps": {
            "unreadable_files": src_gaps["unreadable_files"] + out_gaps["unreadable_files"],
            "binary_or_skipped_lines": (src_gaps["binary_or_skipped_lines"]
                                        + out_gaps["binary_or_skipped_lines"]),
            "source_missing": src_gaps["missing"],
            "output_missing": out_gaps["missing"],
        },
        "status": _STATUS_MEASURED,
    }


def persist_code_volume(project_id: str, record: dict) -> Optional[str]:
    """落盘观察产物，返回工作区相对路径；失败返回 None。

    经 WorkspaceMediator.check_write 单一写闸（D-099⑥），不绕过、不直接碰文件系统边界。
    写失败只 logger.warning + 返回 None（承 `_persist_build_log`）：**不抛不阻断** ——
    观察产物写不下去也绝不能让 P5 的真实验证结论受影响（边界①）。
    """
    try:
        mediator = WorkspaceMediator(str(workspace_path(project_id)))
        target, _risk = mediator.check_write(CODE_VOLUME_REL_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return CODE_VOLUME_REL_PATH
    except Exception:
        logger.warning("code_volume: 观察产物落盘失败（非阻断，P5 验证结论不受影响）：project=%s",
                       project_id, exc_info=True)  # 公理 3
        return None


def measure_and_persist(project_id: str) -> Optional[dict]:
    """P5 旁路接入点：度量一次并落盘。**本函数承诺永不外抛异常**（边界①）。

    调用方（`p5_verification_service.verify_all_hard_required`）不得把返回值并入任何
    SlotVerificationResult、不得让它影响任何判定分支 —— 它是纯旁路观察者。
    返回 None 表示这一轮连观察对象都没构造出来（已告警）。
    """
    try:
        record = measure_code_volume(project_id)
        persist_code_volume(project_id, record)
        return record
    except Exception:
        logger.warning("code_volume: 观察指标整体失败（非阻断）：project=%s",
                       project_id, exc_info=True)  # 公理 3
        return None


def read_code_volume(project_id: str) -> Optional[dict]:
    """只读查询：返回已落盘的观察对象；未度量过或文件不可读 → None。

    刻意**不**加 API 端点（边界②）：本轮无前端消费需求，加端点属 YAGNI；若后续要展示，
    另起需求并按 D-074 做真实联调。
    """
    target = workspace_path(project_id) / CODE_VOLUME_REL_PATH
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("code_volume: 观察产物不可读/损坏，诚实返回 None：project=%s",
                       project_id, exc_info=True)  # 公理 3
        return None
    return data if isinstance(data, dict) else None
