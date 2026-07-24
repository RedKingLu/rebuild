---
name: P-tech-selection
description: P1→P2 gate 技术路线选型（D-109）— LLM 基于 P0/P1 识别的真实源码事实 + 项目目标运行环境（migration_target：CPU 架构/OS）+ 约束，产出「目标技术路线选型建议 + 理由 + 备选」，供用户裁决批准后成为项目红线，贯穿指导 P2 规划 / P4 执行 / 验收
metadata:
  series: P
  phase: P1
  category: stage_skill
  status: planned
  source: rebuild_self
  license: rebuild-internal
---

# P-tech-selection（技术路线选型红线）

## 适用阶段与触发条件

- 阶段：P1 建档完成、提交 **P1→P2 gate** 时。
- 触发：P0/P1 已由 LLM 产出接入/建档识别（tech_stack / dependency / entry_points / config / infra），且项目已在引导期采集 `migration_target`（目标 CPU 架构 + 目标 OS，用户点选）。
- 目标：把「迁移到什么目标技术路线」这一项目级根本约束，像 CPU/OS 红线一样【早定、可裁决、贯穿】。由你（LLM）基于**真实源码事实 + 目标运行环境**综合推荐，用户拍板成为红线。

## 输入（平台已采集/识别，作为你推理的事实底座）

- **P0/P1 识别事实**：`tech_stack`（主语言/框架/运行时/构建系统）、`dependency_draft`（依赖清单）、`entry_points`（应用入口）、`config_inventory`（配置用途，值已脱敏）、`infra_clues`（数据库/中间件/Web 宿主线索）。
- **migration_target**：目标 CPU 架构（如 鲲鹏/ARM64）、目标 OS（如 openEuler）——**硬约束**，你的选型必须能在该目标环境落地。
- 迁移意图原文（若有）与约束。

## 核心原则（硬约束）

1. **接地真实源码**：每一项推荐的 `reasoning` 必须援引输入事实中真实存在的信号（例如"源为 .NET Framework WebForms + SqlClient 连 SQL Server"），**不得输出与源无关的通用模板**（如凭空推荐 Vue3/Spring 而源里没有对应信号）。
2. **服从目标环境红线**：选型必须能在 `migration_target`（CPU/OS）上运行。信创场景优先国产化替代（数据库 → 达梦/openGauss/人大金仓；中间件 → 东方通/宝兰德等），但**以真实源事实与目标环境为准**，不套死清单。
3. **诚实缺口**：证据不足以定论的项，`recommendation` 给出倾向 + 在 `reasoning` 标注不确定性与需用户确认点，不臆造确定性。
4. **脱敏**：任何连接串/密钥/口令值一律不输出。

## 输出契约（严格 JSON，通用锚点固定，样本值你按项目生成）

顶层键（每个"选型项"对象统一为 `{recommendation, reasoning, alternatives}` 三元）：

- `target_language`：对象 `{recommendation, reasoning, alternatives:[{option, reasoning}]}` — 目标语言/主要开发语言。
- `runtime`：对象 `{recommendation, reasoning, alternatives:[...]}` — 目标运行时/SDK 版本（须与目标 OS/CPU 兼容，如 .NET 8 on ARM64/openEuler）。
- `database`：对象 `{recommendation, reasoning, alternatives:[...]}` — 目标数据库（含从源 DB 方言迁移到目标国产库的判断）。
- `web_framework`：对象 `{recommendation, reasoning, alternatives:[...]}` — 目标 Web/应用框架（如 ASP.NET Core MVC 替代 WebForms）。
- `middleware_replacements`：数组，每项 `{component, from, recommendation, reasoning, alternatives:[...]}` — 中间件/外部依赖替换（缓存/消息/Web 宿主/身份等），仅列源中真实出现或明显需要者。
- `key_arch_decisions`：数组，每项 `{decision, recommendation, reasoning, alternatives:[...]}` — 关键架构决策（如分层重构、有状态→无状态、会话存储外置、配置外置等）。
- `overall_rationale`：字符串 — 整体路线一句话理由（如何服从目标环境 + 贴合源事实）。
- `open_questions`：数组，每项字符串 — 需用户裁决/确认的开放问题（证据不足项）。

## 完成后

- 该建议挂到 P1→P2 gate 呈现给用户裁决（approve / 修改）。
- 用户批准后写入 `project.tech_selection` 成为**项目红线**，注入 P2 规划与 P4 执行上下文，并被验收校验（P2/P4 明显偏离选定路线 → 验收发声/门禁拦截）。
