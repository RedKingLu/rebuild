"""Online Source Provider — static provider for community online resources (T6.4 / D-089 / R9-5-4).

留接口：本期仅返回静态卡数据（标注 online_source="static_R15"）。
真实在线来源接入 → R15。

Public API:
  get_static_online_resources(resource_types, stage) -> list[dict]
"""

from __future__ import annotations

from typing import Optional

# Static community card data (同 CommunityPage.tsx 当前 6 个静态卡)
_STATIC_ONLINE_CARDS = [
    {
        "resource_id": "online:ecc-security-guide",
        "name": "ECC Security Guide",
        "resource_type": "knowledge",
        "description": "11 条 Agent 安全基线（隔离/最小权限/审批边界/可观测/Kill Switch）",
        "url": "https://github.com/affaan-m/ECC/blob/main/the-security-guide.md",
        "online_source": "static_R15",
    },
    {
        "resource_id": "online:ecc-coding-standards",
        "name": "ECC Coding Standards",
        "resource_type": "skill",
        "description": "ECC 编码标准——测试优先/安全优先/不可变性/计划先行",
        "url": "https://github.com/affaan-m/ECC",
        "online_source": "static_R15",
    },
    {
        "resource_id": "online:ecc-context-budget",
        "name": "ECC Context Budget",
        "resource_type": "skill",
        "description": "上下文预算管理 Skill（ECC P 系列）",
        "url": "https://github.com/affaan-m/ECC/tree/main/skills/context-budget",
        "online_source": "static_R15",
    },
    {
        "resource_id": "online:ecc-council",
        "name": "ECC Council Pattern",
        "resource_type": "skill",
        "description": "多 Agent 理事会协作模式（ECC P 系列）",
        "url": "https://github.com/affaan-m/ECC/tree/main/skills/council",
        "online_source": "static_R15",
    },
    {
        "resource_id": "online:ecc-pre-commit-hook",
        "name": "ECC Pre-commit Hook",
        "resource_type": "hook",
        "description": "提交前质量检查 Hook（lint/secret/console.log，warn 模式）",
        "url": "https://github.com/affaan-m/ECC",
        "online_source": "static_R15",
    },
    {
        "resource_id": "online:ecc-onboarding-skill",
        "name": "ECC Onboarding Skill",
        "resource_type": "skill",
        "description": "代码库 Onboarding 技能（ECC 来源）",
        "url": "https://github.com/affaan-m/ECC/tree/main/skills/codebase-onboarding",
        "online_source": "static_R15",
    },
]


def get_static_online_resources(
    resource_types: Optional[list[str]] = None,
    stage: str = "all",
) -> list[dict]:
    """Return static online community resource cards.

    These are NOT schedulable locally — require import via T6.1 first (D-089).
    All cards are tagged online_source='static_R15' to indicate pending R15 真实接入.
    """
    if resource_types:
        return [c for c in _STATIC_ONLINE_CARDS if c["resource_type"] in resource_types]
    return list(_STATIC_ONLINE_CARDS)
