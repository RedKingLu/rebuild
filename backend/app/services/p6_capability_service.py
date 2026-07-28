"""P6 交付能力适配器（R17.5-P6-R2 · GAP-P6-4 · capability-first，对称 P5）。

把参考轨要求、P6 stage skill（P-migration-delivery）已描述的"交付能力"，从"纯描述"接线为
【可被探测/登记的能力】。覆盖交付期若干"能力应具备、但本沙箱/无目标环境时无法实际验证"
的交付能力：
  - physical_packaging          物理交付包封装（zip/归档打包）
  - deployment_smoke            部署冒烟 / 目标信创运行时探测（麒麟/统信 + 达梦/openGauss 等）
  - license_network_verification 许可网络核验（联网核实第三方来源 license）
  - package_integrity_external  交付包完整性外部校验（GPG 签名 / 外部校验服务）

capability-first（用户 2026-07-27 裁决，对称 P5-R2）：能力（探测适配器 + 说明）必须真到位；
本沙箱不具备真实执行（本轮维持逐文件下载 Q-P6-3 / 无目标信创运行时 / 无网络核验设施 / 无外部
签名设施）→ 对应能力诚实标 `evidence_gap` 或 `not_applicable`（+ `capability_ready` 记"能力已
接线、待需要时/环境启用"），**非阻断、非门禁、绝不伪造 True/available**；实际执行可不做。

红线（严格遵守，对称 P5-R2 差距计划 §7 + GAP-P6-4）：
  - **事实与判断分层不变**：环境探测是【确定性事实】（有没有 zipfile / 网络 / gpg / 远程主机 /
    目标信创运行时）；能力是否【适用】、是否【达标】的判断由 LLM（advisory 层 / stage skill 引导）
    产出。本服务【不产"通过/pass/completed"结论】。
  - **不参与门禁**：本服务【不读也不改】双向门禁（P5→P6）、脱敏硬门禁、P6 final gate；不新增第二
    核心产物。能力结果作为 `p6_delivery_report.json` 的 `delivery_capabilities` 分区
    （非门禁、非阻断）。交付 completed/blocked 恒由确定性交付内核 + 门禁认定，任何交付能力都
    不得翻转它。
  - **能力适用性不在 Python 硬编码 if-else 判"通过"**（那是 skill/LLM 的判断，D-108 skill-first）；
    本服务只做"能力是否接线 + 环境是否具备"的确定性探测（capability inventory）。
  - **不硬编码样本实例**（MicroOA / openEuler / openGauss / 麒麟 / 统信）——作探测/说明数据，
    随项目替换。
  - **密钥脱敏 D-032**：探测/日志/产物不写任何 Key/Token/Secret（只探测存在性，不读凭据值）。
  - **源只读 D-099**：本服务只探测/只读，不写 source/。
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import socket
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("rebuild.p6_capability")


# ── 交付能力探测结果 DTO ─────────────────────────────────────────────────────

# 能力状态词表（与 P5 十槽位 / 交付门禁分离，避免概念混淆——本层非槽位、非门禁）：
#   available       环境具备且交付设施已启用 → 可产真实交付事实
#   evidence_gap    能力已接线但设施/环境本轮未启用/缺失 → 待需要时或环境真验（非阻断）
#   not_applicable  本沙箱无对应目标（如无任何目标信创运行时绑定）→ 诚实不适用（非阻断）
CAP_AVAILABLE = "available"
CAP_EVIDENCE_GAP = "evidence_gap"
CAP_NOT_APPLICABLE = "not_applicable"


@dataclass
class DeliveryCapability:
    """单个交付能力的接线 + 环境探测结果（确定性事实，非判断、非门禁）。"""
    capability: str                         # 能力名（类别，非刚性枚举）
    capability_wired: bool = True           # 平台侧能力（探测 + 说明 + 待启用设施）是否接线
    capability_ready: bool = True           # 能力就绪（供前端/报告标记；= capability_wired）
    environment_available: bool = False     # 运行/工具环境是否具备（真实探测）
    status: str = CAP_EVIDENCE_GAP          # available / evidence_gap / not_applicable
    probe: dict = field(default_factory=dict)   # 探测明细（确定性事实，可追溯）
    note: str = ""                          # 诚实说明（含"待需要时/环境启用"）
    evidence_refs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "capability_wired": self.capability_wired,
            "capability_ready": self.capability_ready,
            "environment_available": self.environment_available,
            "status": self.status,
            "probe": self.probe,
            "note": self.note,
            "evidence_refs": self.evidence_refs,
        }


class P6CapabilityService:
    """P6 交付能力探测 + 诚实降级（capability-first，对称 P5CapabilityService）。

    确定性探测（无副作用、只读）：有没有 zipfile / 出网 / gpg / 远程执行主机（目标信创运行时）。
    据此给每个交付能力产 available / evidence_gap / not_applicable（+capability_ready），非阻断。
    真实交付事实由确定性交付内核（P6DeliveryService：清单/hash/脱敏/索引/gate）在环境/设施具备时
    产生；本服务【不产"通过/完成"结论】、【不参与任何门禁】。
    """

    def __init__(self, tracer=None):
        self.tracer = tracer

    # ── 底层环境探测（确定性事实，可 mock）─────────────────────────────────

    def _has_zip_capability(self) -> bool:
        """封装能力：Python 标准库 zipfile 是否可用（stdlib 一般都在）。"""
        return importlib.util.find_spec("zipfile") is not None

    def _has_network_access(self, timeout: float = 0.6) -> bool:
        """出网能力：轻量 socket 连通探测（快、异常安全、可 mock）。不做任何鉴权/取值。

        仅探测"能不能出网"这一确定性事实；不访问任何第三方 license 源、不携带凭据。
        """
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=timeout):
                return True
        except Exception:
            # 公理3：探测失败发声但不阻断——无网络是诚实事实，降级为 evidence_gap。
            logger.info("P6 capability: network probe failed (non-blocking, treated as no network)")
            return False

    def _has_gpg(self) -> Optional[str]:
        """外部完整性校验能力：本机是否有 gpg CLI。返回路径或 None。"""
        return shutil.which("gpg")

    def _remote_host_id(self, project_id: str) -> Optional[str]:
        """Workspace 是否绑定远程执行主机（目标信创运行时环境，R14）。"""
        try:
            from app.services.workspace_service import resolve_default_remote_host_id
            return resolve_default_remote_host_id(project_id)
        except Exception:
            logger.warning("P6 capability: remote host resolve failed (non-blocking)",
                           exc_info=True)  # 公理3：发声但不阻断
            return None

    # ── 能力探测（每个能力：能力已接线 + 环境探测 → available/evidence_gap/not_applicable）─

    def probe_physical_packaging(self) -> DeliveryCapability:
        """物理交付包封装（zip/归档）。Q-P6-3 裁决本轮维持逐文件下载 → 诚实待启用。"""
        has_zip = self._has_zip_capability()
        cap = DeliveryCapability(capability="physical_packaging",
                                 environment_available=has_zip,
                                 probe={"zipfile_available": has_zip})
        # 反伪造：即便 zipfile 就位，本轮按 Q-P6-3 维持逐文件下载（+/p6/download 脱敏+SHA-256），
        # 物理封装设施【未启用】→ 诚实 evidence_gap（能力已接线待需要时启用），绝不标"已封装/available"。
        cap.status = CAP_EVIDENCE_GAP
        if has_zip:
            cap.note = ("物理交付包封装能力已接线（zipfile 可用）；本轮按 Q-P6-3 裁决维持逐文件下载"
                        "（p6_delivery_report.json + /p6/download 脱敏 + SHA-256），封装设施待需要时"
                        "启用；诚实标 evidence_gap，非阻断、不伪造已封装。")
        else:
            cap.note = ("无 zipfile 打包能力：物理交付包封装待具备打包工具的环境启用；诚实 evidence_gap，"
                        "非阻断。本轮本就维持逐文件下载（Q-P6-3）。")
        return cap

    def probe_deployment_smoke(self, project_id: str) -> DeliveryCapability:
        """部署冒烟 / 目标信创运行时探测（麒麟/统信 + 达梦/openGauss 等，随项目替换）。"""
        host = self._remote_host_id(project_id)
        cap = DeliveryCapability(capability="deployment_smoke",
                                 environment_available=bool(host),
                                 probe={"remote_host_bound": bool(host)})
        if host:
            # 有目标运行时绑定：设施待真验（本轮不实际跑冒烟）→ evidence_gap，不臆测已部署通过。
            cap.status = CAP_EVIDENCE_GAP
            cap.note = ("已绑定远程执行主机（目标运行时）：部署冒烟能力已接线，待在目标信创环境真跑"
                        "冒烟产退出码/健康检查事实；诚实 evidence_gap，非阻断，绝不臆测已部署验证通过。")
        else:
            # 无任何目标信创运行时绑定：本沙箱不适用 → not_applicable，绝不臆测已部署通过。
            cap.status = CAP_NOT_APPLICABLE
            cap.note = ("无目标信创运行时绑定（麒麟/统信 + 达梦/openGauss 等不在本沙箱）：部署冒烟"
                        "本轮不适用（not_applicable），待绑定目标环境后启用；绝不臆测已部署验证通过。")
        return cap

    def probe_license_network_verification(self) -> DeliveryCapability:
        """许可网络核验（联网核实第三方来源 license）。许可文件确定性检测本身留 R3。"""
        online = self._has_network_access()
        cap = DeliveryCapability(capability="license_network_verification",
                                 environment_available=online,
                                 probe={"network_reachable": online})
        # 反伪造：即便出网，本轮无"第三方来源 license 联网核验设施"（该确定性检测留 R3）→
        # 网络仅为前置条件，核验设施未启用 → 诚实 evidence_gap，绝不标"已核实许可"。
        cap.status = CAP_EVIDENCE_GAP
        if online:
            cap.note = ("出网前置条件具备（网络可达）：许可网络核验能力已接线，待启用第三方来源 license"
                        "联网核实设施（确定性许可文件检测见后续轮次）；诚实 evidence_gap，非阻断，"
                        "不伪造已核实。")
        else:
            cap.note = ("无出网能力：许可网络核验（联网核实第三方来源 license）无法执行，诚实 evidence_gap，"
                        "非阻断；许可确定性文件检测由后续轮次承载，本轮不臆测许可清晰。")
        return cap

    def probe_package_integrity_external(self) -> DeliveryCapability:
        """交付包完整性外部校验（GPG 签名 / 外部校验服务）。内部 SHA-256 已由交付内核产出。"""
        gpg = self._has_gpg()
        cap = DeliveryCapability(capability="package_integrity_external",
                                 environment_available=bool(gpg),
                                 probe={"gpg_binary": bool(gpg)})
        # 反伪造：内部 SHA-256 hash_manifest 由确定性交付内核产出（非本能力）；外部签名/校验需签名密钥
        # 与外部校验设施，本轮未启用 → 诚实 evidence_gap，绝不标"已外部签名/校验通过"。
        cap.status = CAP_EVIDENCE_GAP
        if gpg:
            cap.note = ("gpg 工具就位：交付包完整性外部校验（签名/验签）能力已接线，待启用签名密钥与外部"
                        "校验设施真验；内部 SHA-256 hash_manifest 已由确定性交付内核产出。诚实 evidence_gap，"
                        "非阻断，不伪造已外部签名。")
        else:
            cap.note = ("无 gpg / 外部校验设施：交付包完整性外部校验待具备签名/校验环境启用；内部 SHA-256"
                        " hash_manifest 已由确定性交付内核产出。诚实 evidence_gap，非阻断。")
        return cap

    # ── 单能力调用 ─────────────────────────────────────────────────────────

    _DISPATCH = {
        "physical_packaging": "probe_physical_packaging",
        "deployment_smoke": "probe_deployment_smoke",
        "license_network_verification": "probe_license_network_verification",
        "package_integrity_external": "probe_package_integrity_external",
    }

    def known_capabilities(self) -> list:
        return list(self._DISPATCH.keys())

    def verify_capability(self, project_id: str, capability: str) -> DeliveryCapability:
        """探测单个交付能力 + 环境（确定性事实）。未知能力 → 诚实说明，非崩。"""
        name = (capability or "").strip().lower()
        method = self._DISPATCH.get(name)
        if method is None:
            return DeliveryCapability(
                capability=name or "unknown", capability_wired=False, capability_ready=False,
                status=CAP_EVIDENCE_GAP,
                probe={"known_capabilities": self.known_capabilities()},
                note=(f"未接线的交付能力 '{name}'；已接线能力见 probe.known_capabilities。"
                      "能力适用性由 P6 stage skill / LLM 判断，本工具只报能力与环境探测。"))
        fn = getattr(self, method)
        try:
            # probe_deployment_smoke 需 project_id；其余不需。
            if method == "probe_deployment_smoke":
                return fn(project_id)
            return fn()
        except Exception as e:
            # 公理3：任何探测异常都诚实降级，绝不拖垮 P6 交付。
            logger.warning("P6 capability probe '%s' failed (non-blocking): %s", name, e,
                           exc_info=True)
            return DeliveryCapability(
                capability=name, status=CAP_EVIDENCE_GAP,
                probe={"probe_error": type(e).__name__},
                note=f"交付能力 '{name}' 探测异常，诚实降级为 evidence_gap（非阻断，不伪造）。")

    def probe_all(self, project_id: str) -> dict:
        """探测全部已接线交付能力，产 capability inventory（写入 p6_delivery_report 分区，非门禁）。"""
        caps = [self.verify_capability(project_id, c) for c in self.known_capabilities()]
        available = sum(1 for c in caps if c.status == CAP_AVAILABLE)
        gap = sum(1 for c in caps if c.status == CAP_EVIDENCE_GAP)
        na = sum(1 for c in caps if c.status == CAP_NOT_APPLICABLE)
        return {
            "note": ("交付能力清单（capability-first）：capability_ready=能力已接线；"
                     "environment_available=运行/工具环境是否具备。本分区为【非门禁】能力/环境事实，"
                     "不参与交付 completed/blocked，不翻转双向门禁/脱敏门禁/final gate；能力是否适用/达标"
                     "由 P6 skill / LLM 判断。环境/设施缺失能力诚实标 evidence_gap（待需要时/环境启用）"
                     "或 not_applicable（无目标环境），非阻断、绝不伪造已交付/已验证。"),
            "capabilities": [c.to_dict() for c in caps],
            "summary": {"total": len(caps), "environment_available": available,
                        "evidence_gap": gap, "not_applicable": na},
        }
