---
name: P-coding-standards
description: 跨阶段 — 迁移后生成代码的编码规范基线，按目标栈（C#/.NET、Java、国产框架）落地
metadata:
  series: P
  phase: cross
  category: review_skill
  status: platform_runtime
  source: ECC skills/coding-standards (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-coding-standards（编码规范基线）

## 适用阶段与触发条件
- 阶段：跨阶段（重点 P4 转换 → P5 验证 → P6 交付）。约束迁移后生成代码的质量基线。
- 触发：生成/改写目标栈代码后、代码评审前、提交前格式检查、新建模块时确定约定。
- 不适用：未涉及代码产出的纯调研/选型阶段（可由 P-council 处理）。

## 输入
- 目标栈：C#/.NET、Java、或国产框架（如东方通中间件适配、国产 ORM）。
- 既有项目约定：命名风格、目录结构、lint/format 配置（如 .editorconfig、Checkstyle、dotnet format）。
- 待检查的迁移产出代码与对应的遗留原代码（用于语义对齐）。

## 执行步骤
1. **确定目标栈规范**：不照搬 JS/TS 习惯；C#/.NET 用 PascalCase/驼峰与 .NET 命名约定，Java 用 Java 规范，按栈选基线。
2. **基本原则检查**：KISS（避免过度设计）、DRY（消除重复）、YAGNI（不写当前不需要的抽象）。
3. **命名与结构**：标识符语义清晰、与领域一致；目录/命名空间与目标栈惯例对齐。
4. **不可变与校验**：优先不可变数据/防御性校验；公共入口参数校验，边界值与空值处理明确。
5. **自动修复 vs 标注**：可机器修复的格式问题自动修复；需人工判断的设计/风格问题标注交评审。
6. **与栈专属 Skill 配合**：调用 P-dotnet-patterns / P-backend-patterns 落实栈内细则，本 Skill 提供跨栈基线。

## 输出 / 产物（Artifact / Evidence）
- 规范检查报告（Artifact）：违规项清单、严重级别、可否自动修复。
- 修复 diff（Artifact）：自动修复的格式/约定变更。
- 待人工项清单（Artifact）：设计/风格类需评审的问题。
- 证据（Evidence）：lint/format 工具的实际输出；模型对代码风格的判断为建议，不充当 Evidence。
- 写入 Trace/Audit 并登记 Registry。

## 质量门 / 验收标准
- 检查规则与目标栈匹配，未误用其他语言惯例。
- 自动修复不改变程序语义（仅格式/命名等安全变更）；语义相关改动须人工确认。
- 报告区分「已自动修复」与「待人工判断」。
- 引用真实工具输出作为证据，而非仅凭模型判断。

## 场景要点（按项目场景取用）
- 遗留 .NET/Java 代码迁移后须保持领域命名与原系统一致，避免业务语义漂移。
- 目标栈若为国产框架/中间件，遵循其官方约定，不强行套用开源主流框架习惯。
- 字符集、编码声明、日期/数字 locale 处理在国产 OS 上须规范统一（UTF-8、显式时区）。
- 异常与日志规范须满足后续 Audit/合规取证需求。

## 反例 / 禁止
- 禁止把 JS/TS（分号、camelCase 文件名等）习惯套到 C#/.NET 或 Java。
- 禁止以「格式化」名义做改变语义的重写。
- 禁止过度抽象（违反 YAGNI）以追求「优雅」。
- 禁止在代码或注释中硬编码凭据/连接串/Token。

## 与平台集成
- 模型对风格的判断经 ModelGateway 产出，仅作建议，用量计入预算。
- 检查/修复结果 → Artifact/Evidence + Trace/Audit + Registry，模型输出 ≠ Evidence。
- 高风险或语义相关改动走 HITL Gate 由用户确认。
- 与 P-dotnet-patterns、P-backend-patterns 形成「跨栈基线 + 栈内细则」分层。

## 参考
- ECC skills/coding-standards（MIT, https://github.com/affaan-m/ECC）
- 栈专属 Skill：P-dotnet-patterns、P-backend-patterns
- 平台 `04-模型与资源/04-平台资源与Registry规范.md`
