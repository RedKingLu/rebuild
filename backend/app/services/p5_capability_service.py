"""P5 验证维度能力适配器（R17.5-P5-R2 · GAP-P5-1 + GAP-P5-3 · capability-first）。

把参考轨要求、P5 stage skill（P-migration-verification）已描述的验证维度，从"纯描述"
接线为【可被 agent 调用的能力】。覆盖：浏览器/E2E QA（P-browser-qa）、行为等价/断言
（P-eval-harness）、DB 结构+数据迁移核对、业务闭环（登录/RBAC/动态表单/审批）、回归对
P1 金标准（D-106）、性能基准（P-benchmark）、.NET build/test/static（GAP-P5-3）。

capability-first（用户 2026-07-27 裁决）：能力（探测适配器 + 执行 hook + 工具 + skill 引导）
必须真到位；本地环境不具备真实执行（无 dotnet SDK / 无远程 DB / 无可跑应用 / 无浏览器运行
环境）→ 对应维度诚实标 `evidence_gap`（+ `capability_ready=true`，记"能力已具备、待环境
真验"），**非阻断、绝不伪造**；实际执行可不做。

红线（严格遵守，见 R17.5-P5-分轮次施工计划 §0 红线 + 差距计划 §6）：
  - **事实与判断分层不变**：环境探测是【确定性事实】（有没有 dotnet / 浏览器 / 远程主机 /
    P1 金标准）；维度是否【适用】、是否【达标】的判断由 LLM（advisory 层 / stage skill 引导）
    产出。本服务【不产"通过/pass"结论】。
  - **不参与门禁**：本服务【不读也不改】`can_mark_completed`、不改十槽位状态、不新增槽位、
    不新增第二核心产物。维度能力结果作为 `p5_validation_report.json` 的
    `dimension_capabilities` 分区（非门禁、非阻断）。can_be_completed 恒由确定性门禁认定，
    任何维度能力都不得翻转它。
  - **维度适用性不在 Python 硬编码 if-else**（那是 skill/LLM 的判断，D-108 skill-first）；
    本服务只做"能力是否接线 + 环境是否具备"的确定性探测（capability inventory）。
  - **密钥脱敏 D-032**：探测/日志/产物不写任何 Key/Token/Secret（只探测存在性，不读凭据值）。
  - **源只读 D-099**：本服务只探测/只读，不写 source/。
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("rebuild.p5_capability")


# ── 维度能力探测结果 DTO ────────────────────────────────────────────────────

# 能力状态词表（与十槽位 P5SlotStatus 分离，避免概念混淆——本层非槽位、非门禁）：
#   available      环境具备 → 可经对应子 skill 工作流/命令产真实事实
#   evidence_gap   能力已接线但环境缺失（无 SDK/DB/浏览器/可跑应用）→ 待环境真验（非阻断）
CAP_AVAILABLE = "available"
CAP_EVIDENCE_GAP = "evidence_gap"


@dataclass
class DimensionCapability:
    """单个验证维度的能力接线 + 环境探测结果（确定性事实，非判断、非门禁）。"""
    dimension: str                          # 维度名（能力类别，非刚性槽位）
    sub_skill: str = ""                     # 关联子 skill（引导 agent 走哪套工作流）
    capability_wired: bool = True           # 平台侧能力（适配器 + 工具 + skill）是否接线
    capability_ready: bool = True           # 能力就绪（= capability_wired，供前端/报告标记）
    environment_available: bool = False     # 运行环境是否具备（真实探测）
    status: str = CAP_EVIDENCE_GAP          # available / evidence_gap
    probe: dict = field(default_factory=dict)   # 探测明细（确定性事实，可追溯）
    note: str = ""                          # 诚实说明（含"待环境真验"）
    evidence_refs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "sub_skill": self.sub_skill,
            "capability_wired": self.capability_wired,
            "capability_ready": self.capability_ready,
            "environment_available": self.environment_available,
            "status": self.status,
            "probe": self.probe,
            "note": self.note,
            "evidence_refs": self.evidence_refs,
        }


class P5CapabilityService:
    """P5 验证维度能力探测 + 诚实降级。

    确定性探测（无副作用、只读）：有没有 dotnet SDK / 浏览器自动化 / 远程执行主机 /
    P1 金标准产物。据此给每个维度产 available / evidence_gap（+capability_ready），
    非阻断。真实执行事实由对应子 skill 工作流 + 确定性命令/工具在环境具备时产生。
    """

    def __init__(self, tracer=None):
        self.tracer = tracer

    # ── 底层环境探测（确定性事实，可 mock）────────────────────────────────

    def _has_dotnet_sdk(self) -> Optional[str]:
        """本机是否有 dotnet CLI（GAP-P5-3）。返回路径或 None。"""
        return shutil.which("dotnet")

    def _has_browser_automation(self) -> dict:
        """浏览器自动化能力：playwright 包 + 浏览器二进制。"""
        pw = importlib.util.find_spec("playwright") is not None
        browser = next((b for b in ("chromium", "google-chrome", "chromium-browser",
                                    "chrome", "msedge") if shutil.which(b)), None)
        return {"playwright_installed": pw, "browser_binary": browser}

    def _remote_host_id(self, project_id: str) -> Optional[str]:
        """Workspace 是否绑定远程执行主机（跑真实应用/DB 的环境，R14）。"""
        try:
            from app.services.workspace_service import resolve_default_remote_host_id
            return resolve_default_remote_host_id(project_id)
        except Exception:
            logger.warning("P5 capability: remote host resolve failed (non-blocking)",
                           exc_info=True)  # 公理3：发声但不阻断
            return None

    def _p1_baseline_present(self, project_id: str) -> dict:
        """P1 金标准（acceptance_baseline，D-106）是否已产出，供回归/行为等价作锚点。"""
        try:
            from app.services.workspace_service import workspace_path
            fp = workspace_path(project_id) / "artifacts" / "p1" / "acceptance_baseline.json"
            return {"present": fp.exists(),
                    "ref": "artifacts/p1/acceptance_baseline.json" if fp.exists() else ""}
        except Exception:
            logger.warning("P5 capability: p1 baseline probe failed (non-blocking)", exc_info=True)
            return {"present": False, "ref": ""}

    # ── 维度探测（每个维度：能力已接线 + 环境探测 → available / evidence_gap）─

    def probe_dotnet_build(self) -> DimensionCapability:
        """GAP-P5-3：.NET build/test/static。SDK 在则条件槽走真实命令；不在则诚实待环境真验。"""
        sdk = self._has_dotnet_sdk()
        cap = DimensionCapability(dimension="dotnet_build", sub_skill="")
        if sdk:
            cap.environment_available = True
            cap.status = CAP_AVAILABLE
            cap.probe = {"dotnet_sdk": "present"}
            cap.note = ("dotnet SDK 已就位：build/test/static 由条件槽 SDK-aware 命令补全经 "
                        "ExecutionProvider 真实执行产退出码事实。")
        else:
            cap.status = CAP_EVIDENCE_GAP
            cap.probe = {"dotnet_sdk": "absent"}
            cap.note = ("本地无 dotnet SDK：.NET build/test/static 能力已接线（条件槽 SDK-aware "
                        "命令补全 + 可绑定远程执行主机），待具备 SDK / 远程主机的环境真验；"
                        "当前诚实标 evidence_gap，非阻断、不伪造通过。")
        return cap

    def probe_browser_qa(self) -> DimensionCapability:
        """浏览器 / E2E QA（P-browser-qa）：Playwright 真实走查 + 截图。"""
        probe = self._has_browser_automation()
        available = bool(probe.get("playwright_installed") and probe.get("browser_binary"))
        cap = DimensionCapability(dimension="browser_qa", sub_skill="P-browser-qa",
                                  environment_available=available, probe=probe)
        if available:
            cap.status = CAP_AVAILABLE
            cap.note = ("浏览器自动化就位：按 P-browser-qa 工作流对关键路径回放 + 截图 diff + "
                        "交互断言产真实事实（业务结果断言，非 API 200 即通过）。")
        else:
            cap.status = CAP_EVIDENCE_GAP
            cap.note = ("无浏览器运行环境（缺 Playwright / 浏览器二进制）：E2E QA 能力已接线（"
                        "P-browser-qa + webapp-testing），待浏览器环境（含国产内核）真验；诚实 "
                        "evidence_gap，非阻断。")
        return cap

    def probe_eval_harness(self, project_id: str) -> DimensionCapability:
        """行为等价 / 断言（P-eval-harness）：需可跑目标应用产等价事实。"""
        host = self._remote_host_id(project_id)
        available = bool(host)
        cap = DimensionCapability(dimension="eval_harness", sub_skill="P-eval-harness",
                                  environment_available=available,
                                  probe={"remote_host_bound": bool(host)})
        if available:
            cap.status = CAP_AVAILABLE
            cap.note = ("有可跑环境（远程执行主机绑定）：按 P-eval-harness 定义等价/契约/数据一致"
                        "断言并批量执行产 pass@k 事实（code grader 判定，model grader 需人工抽检）。")
        else:
            cap.status = CAP_EVIDENCE_GAP
            cap.note = ("无可跑目标应用（未绑定远程执行主机）：行为等价/断言能力已接线（"
                        "P-eval-harness），断言集可先行定义，执行待环境真验；诚实 evidence_gap，非阻断。")
        return cap

    def probe_db_migration(self, project_id: str) -> DimensionCapability:
        """DB 结构 + 数据迁移：表/列结构核对、行数、抽样比对。需远程 DB 连接。"""
        host = self._remote_host_id(project_id)
        available = bool(host)
        cap = DimensionCapability(dimension="db_migration", sub_skill="P-eval-harness",
                                  environment_available=available,
                                  probe={"remote_host_bound": bool(host)})
        if available:
            cap.status = CAP_AVAILABLE
            cap.note = ("有远程执行主机：可经 ExecutionProvider 对目标库跑结构核对 + 行数 count + "
                        "抽样比对产真实事实（源/目标类型映射与精度差异按业务容差判定）。")
        else:
            cap.status = CAP_EVIDENCE_GAP
            cap.note = ("无远程 DB / 执行主机绑定：DB 结构+数据迁移核对能力已接线，待绑定"
                        "达梦/人大金仓/openGauss 等目标库的环境真验；诚实 evidence_gap，非阻断。")
        return cap

    def probe_business_flow(self, project_id: str) -> DimensionCapability:
        """业务闭环（登录/RBAC/动态表单/审批）：需可跑应用 + 浏览器。"""
        host = self._remote_host_id(project_id)
        browser = self._has_browser_automation()
        available = bool(host and browser.get("playwright_installed") and browser.get("browser_binary"))
        cap = DimensionCapability(dimension="business_flow", sub_skill="P-browser-qa",
                                  environment_available=available,
                                  probe={"remote_host_bound": bool(host), **browser})
        if available:
            cap.status = CAP_AVAILABLE
            cap.note = ("有可跑应用 + 浏览器：按 P-browser-qa 走登录/RBAC/动态表单/审批端到端闭环"
                        "并断言业务结果产真实事实。")
        else:
            cap.status = CAP_EVIDENCE_GAP
            cap.note = ("无可跑应用或无浏览器：业务闭环（登录/RBAC/动态表单/审批）能力已接线，"
                        "待部署可访问目标应用 + 浏览器环境真验；诚实 evidence_gap，非阻断。")
        return cap

    def probe_regression_baseline(self, project_id: str) -> DimensionCapability:
        """回归对 P1 金标准（D-106）：以 P1 acceptance_baseline 为锚点做行为等价/回归比对。"""
        baseline = self._p1_baseline_present(project_id)
        host = self._remote_host_id(project_id)
        # 有金标准 + 有可跑环境 才能执行真实回归比对；缺任一 → 诚实 evidence_gap。
        available = bool(baseline.get("present") and host)
        cap = DimensionCapability(dimension="regression_baseline", sub_skill="P-eval-harness",
                                  environment_available=available,
                                  probe={"p1_baseline_present": baseline.get("present"),
                                         "p1_baseline_ref": baseline.get("ref"),
                                         "remote_host_bound": bool(host)})
        if available:
            cap.status = CAP_AVAILABLE
            cap.note = ("P1 金标准（D-106）已产出 + 有可跑环境：可对 P1 acceptance_baseline 做行为"
                        "等价/回归比对产真实事实。")
        else:
            miss = []
            if not baseline.get("present"):
                miss.append("缺 P1 acceptance_baseline 金标准")
            if not host:
                miss.append("无可跑目标环境")
            cap.status = CAP_EVIDENCE_GAP
            cap.note = ("回归对 P1 金标准（D-106）能力已接线（P-eval-harness + P1 baseline 锚点），"
                        f"待补齐：{'、'.join(miss)}；诚实 evidence_gap，非阻断。")
        return cap

    def probe_benchmark(self, project_id: str) -> DimensionCapability:
        """性能基准（P-benchmark）：源基线 + 信创栈复测，需可跑应用。"""
        host = self._remote_host_id(project_id)
        available = bool(host)
        cap = DimensionCapability(dimension="benchmark", sub_skill="P-benchmark",
                                  environment_available=available,
                                  probe={"remote_host_bound": bool(host)})
        if available:
            cap.status = CAP_AVAILABLE
            cap.note = ("有可跑环境：按 P-benchmark 建源基线 + 信创栈复测，以百分位（p95/p99）产真实"
                        "性能事实（架构/DB 差异显式记录）。")
        else:
            cap.status = CAP_EVIDENCE_GAP
            cap.note = ("无可跑应用（未绑定执行主机）：性能基准能力已接线（P-benchmark），待部署"
                        "源/目标可测环境真验；诚实 evidence_gap，非阻断，不以模型预估数值冒充实测。")
        return cap

    # ── 单维度调用（供 p5_verify_dimension 工具）────────────────────────────

    _DISPATCH = {
        "dotnet_build": "probe_dotnet_build",
        "browser_qa": "probe_browser_qa",
        "eval_harness": "probe_eval_harness",
        "db_migration": "probe_db_migration",
        "business_flow": "probe_business_flow",
        "regression_baseline": "probe_regression_baseline",
        "benchmark": "probe_benchmark",
    }

    def known_dimensions(self) -> list:
        return list(self._DISPATCH.keys())

    def verify_dimension(self, project_id: str, dimension: str) -> DimensionCapability:
        """探测单个维度能力 + 环境（确定性事实）。未知维度 → 诚实说明，非崩。"""
        name = (dimension or "").strip().lower()
        method = self._DISPATCH.get(name)
        if method is None:
            return DimensionCapability(
                dimension=name or "unknown", capability_wired=False, capability_ready=False,
                status=CAP_EVIDENCE_GAP,
                probe={"known_dimensions": self.known_dimensions()},
                note=(f"未接线的验证维度 '{name}'；已接线维度见 probe.known_dimensions。"
                      "维度适用性由 P5 stage skill / LLM 判断，本工具只报能力与环境探测。"))
        fn = getattr(self, method)
        try:
            # probe_dotnet_build / probe_browser_qa 不需 project_id
            if method in ("probe_dotnet_build", "probe_browser_qa"):
                return fn()
            return fn(project_id)
        except Exception as e:
            # 公理3：任何探测异常都诚实降级，绝不拖垮 P5。
            logger.warning("P5 capability probe '%s' failed (non-blocking): %s", name, e,
                           exc_info=True)
            return DimensionCapability(
                dimension=name, status=CAP_EVIDENCE_GAP,
                probe={"probe_error": type(e).__name__},
                note=f"维度 '{name}' 能力探测异常，诚实降级为 evidence_gap（非阻断，不伪造）。")

    def probe_all(self, project_id: str) -> dict:
        """探测全部已接线维度，产 capability inventory（写入 p5_validation_report 分区，非门禁）。"""
        caps = [self.verify_dimension(project_id, d) for d in self.known_dimensions()]
        available = sum(1 for c in caps if c.status == CAP_AVAILABLE)
        gap = sum(1 for c in caps if c.status == CAP_EVIDENCE_GAP)
        return {
            "note": ("验证维度能力清单（capability-first）：capability_ready=能力已接线；"
                     "environment_available=运行环境是否具备。本分区为【非门禁】能力/环境事实，"
                     "不参与 can_be_completed；维度是否适用由 P5 skill / LLM 判断。"
                     "环境缺失维度诚实标 evidence_gap（待环境真验），非阻断、不伪造。"),
            "dimensions": [c.to_dict() for c in caps],
            "summary": {"total": len(caps), "environment_available": available,
                        "evidence_gap": gap},
        }
