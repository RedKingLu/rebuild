---
name: P-documentation-lookup
description: P1 建档 — 检索达梦/openGauss/GaussDB/国产中间件官方迁移指南与兼容性矩阵，以权威文档替代臆测
metadata:
  series: P
  phase: P1
  category: resource_skill
  status: platform_runtime
  source: ECC skills/documentation-lookup (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-documentation-lookup（权威文档检索）

## 适用阶段与触发条件

- 阶段：P1 建档（也服务于 P2 评估的兼容性判断）。
- 触发：遇到信创目标栈的兼容性 / 语法 / API / 版本特性问题，且训练数据不足以可靠回答时。
- 核心前提：达梦 DM / openGauss / GaussDB / 东方通 / 宝兰德 / 麒麟 / 统信等国产栈在模型训练语料中稀缺、版本演进快，必须查权威文档而非凭记忆作答。

## 输入

- 待查问题（具体到组件 + 版本 + 特性，如「达梦 DM8 是否支持 MERGE INTO 语法」）。
- P0 识别出的源栈版本与目标栈选型。
- 受控检索工具 / MCP 文档源句柄（官方文档站、兼容性矩阵、迁移白皮书）。

## 执行步骤

1. **把问题收敛为可查询条目**：明确组件、版本、特性、源对端（如 Oracle 12c PL/SQL `BULK COLLECT` → 达梦 DM8 对应能力）。
2. **优先权威源**：官方文档 / 官方迁移指南 / 官方兼容性矩阵 > 厂商技术白皮书 > 社区经验 > 模型推断（推断仅作旁证，不作结论）。
3. **受控检索**：经平台允许的 MCP / 检索工具发起查询，每个问题原则上 ≤3 次检索调用；命中即止，避免无界抓取。
4. **版本对齐核对**：确认检索到的文档版本与项目实际版本一致；版本不符的结论降级为「待确认」。
5. **登记证据**：记录来源 URL / 文档名、版本、检索时间、原文要点；无法确认的写入「待确认兼容性清单」。

## 输出 / 产物（Artifact / Evidence）

- 技术文档索引（按目标组件组织，含来源 URL、版本、检索时间）。
- 兼容性结论摘要（兼容 / 部分兼容需改写 / 不兼容需替换 / 待确认）。
- 「待确认兼容性清单」：需用户或厂商进一步确认的条目。
- 索引与摘要挂 Project/Stage，附 Trace（检索动作）/ Audit；检索命中的原文为 Evidence。

## 质量门 / 验收标准

- 每条兼容性结论都有可点击 / 可复核的权威来源，并标注版本与检索时间。
- 无来源支撑的论断不得作为结论输出，只能进「待确认清单」。
- 单问题检索调用次数受控（≤3），避免抓取泛滥。
- 检索版本与项目实际版本一致，不一致已显式降级。

## 场景要点（按项目场景取用）

- Oracle→达梦 DM：重点查兼容模式（Oracle 兼容 / MySQL 兼容）、PL/SQL 包 / 序列 / 同义词 / DBLink 支持差异。
- Oracle→openGauss / GaussDB：查 A 兼容模式、分区表、存储过程、`ROWNUM`/`DECODE`/`NVL` 等函数对应。
- MSSQL→国产库：查 T-SQL 特性（`TOP`、`IDENTITY`、`OUTPUT`、CTE、窗口函数）对端支持与改写方式。
- 中间件：WebLogic/WebSphere→东方通 TongWeb / 宝兰德，查 JNDI、数据源、JMS、类加载、JDK 版本要求。
- OS：麒麟 / 统信查 CPU 架构（鲲鹏 / 飞腾 / 海光 / 兆芯）对应的 JDK、依赖库、字符集与时区默认值。
- 凡官方文档未明确支持的特性，一律标「待确认」，不替用户拍板。

## 反例 / 禁止

- 禁止凭训练记忆直接断言国产库支持某语法 / 函数而不附文档来源。
- 禁止把社区博客 / 论坛帖当作权威结论（可作旁证，须标来源等级）。
- 禁止无界抓取或对单一问题反复检索刷调用。
- 禁止在索引中记录任何访问凭据 / Key/Token。

## 与平台集成

- 检索结论的归纳 / 摘要经 ModelGateway 调用模型，不直接调 Provider；检索本身经受控 MCP / 工具。
- 外部网络检索属受控外部操作，按平台 Gate 策略执行；涉及付费 / 受限文档源须用户授权。
- 文档索引与兼容性摘要挂 Project/Run/Stage/Node，生成 Artifact/Evidence 并附 Trace/Audit/Registry。
- 模型对文档的解读属辅助，不是 Evidence；Evidence 是检索命中的官方原文。
- 自检不替代 Acceptance 验收；社区 Skill / 外部文档默认只读引用。

## 参考

- ECC `skills/documentation-lookup`（MIT, https://github.com/affaan-m/ECC）
- rebuild 文档：`文档/04-模型与资源/`（资源与检索工具接入）、`文档/00-项目治理/`
