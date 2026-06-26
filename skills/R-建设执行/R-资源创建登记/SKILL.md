---
name: R-资源创建登记
description: rebuild 特有 — Agent/Skill/Tool/Hook/Case/Knowledge/Template/MCP/ExpertAgent/Policy/确定性转换/ExecutionProvider 的资源创建与 Registry 登记流程
metadata:
  series: R
  category: construction_skill
  status: active
  skill_source: rebuild_self
---

# R-资源创建登记

## 使用场景
rebuild 平台自身建设过程中，创建新的 Agent/Skill/Tool/Hook/Case/Knowledge/Template/MCP/Expert Agent/Policy/确定性转换/ExecutionProvider 资源时使用本 Skill。

## 登记流程

1. **确定资源类型**：从 12 类中选择（agent/skill/tool/hook/case/knowledge/template/mcp/expert_agent/policy/deterministic_transformer/execution_provider）
2. **确定来源与信任等级**：internal_current / community / external_online / user_provided 等
3. **确定风险级别**：L0（只读）→ L5（高风险写操作）
4. **确定状态**：draft / active / read_only / planned / local_existing / not_connected / platform_runtime
5. **填写 Resource Registry 条目**：通过 POST /api/resources 登记
6. **填写领域专属表**：Agent → AgentDefinition / Skill → SkillDefinition
7. **前端可查**：确认 ResourcesPage 能展示该资源

## 状态口径

| 状态 | 含义 |
|---|---|
| active | 已施工、可用 |
| read_only | 只读参考（Case/Knowledge/Template 永远此状态） |
| planned | 计划创建、尚未施工 |
| platform_runtime | 平台运行期 Skill（R9-R12 创建完整内容） |
| local_existing | 本地 CC 已有、不重复创建 R 系列副本 |
| not_connected | 接口位预留、依赖后续 R 阶段 |

## 关联

- `04-模型与资源/04-平台资源与Registry规范.md`（Registry 详述源）
- `AGENTS.md` §14（Agent/Skill 切换条件）
