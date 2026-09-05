---
name: P-automation-audit-ops
description: 跨阶段（P5–P6）— 证据优先盘点迁移项目的自动化（定时任务/Hook/连接器）并分类处置
metadata:
  series: P
  phase: cross
  category: validation_skill
  status: platform_runtime
  source: ECC skills/automation-audit-ops (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-automation-audit-ops（自动化审计盘点）

## 适用阶段与触发条件
- 阶段：跨阶段，重点 P5 验证 → P6 交付。迁移前后须清查遗留与新建的自动化（cron/批处理/Hook/集成连接器）。
- 触发：迁移前盘点遗留定时任务/批处理、迁移后验证自动化是否正确迁移、上线前审计、交付收口。
- 不适用：无自动化/无定时任务的纯静态交付。

## 输入
- 自动化清单来源：crontab、系统计划任务、应用内调度（Quartz/Hangfire）、平台 Hook、外部集成连接器、ETL/批处理脚本。
- 每项的证据：实际配置、最近执行记录/日志、依赖的库表与接口、负责人。
- 迁移目标环境约束：国产 OS 调度差异、达梦/openGauss/GaussDB 上的批作业、国产中间件触发机制。

## 执行步骤
1. **证据优先采集**：以实际配置与执行日志为准列出全部自动化，不依据文档臆断「应该有」。
2. **存活判定**：逐项判定 live（在跑且有近期执行证据）/ broken（配置在但执行失败/无日志）/ redundant（与他项重复）/ missing（遗留有、迁移后缺失）。
3. **依赖映射**：标注每项依赖的库表、接口、文件、凭据来源（凭据本身不落盘、不打印）。
4. **处置分类**：对应 keep（保留）/ merge（合并冗余）/ cut（下线无用）/ fix（修复 broken/补 missing）。
5. **生成审计 Trail**：每项结论附证据链（配置截取 + 日志时间戳 + 依赖），可追溯。
6. **高风险路由**：下线/修改生产自动化属外部写（L4–L5），出建议交用户 Gate，不自行变更。

## 输出 / 产物（Artifact / Evidence）
- 自动化盘点表（Artifact）：每项的状态（live/broken/redundant/missing）与处置（keep/merge/cut/fix）。
- 审计 Trail（Artifact/Evidence）：结论 + 支撑证据（实际配置、执行日志时间戳、依赖映射）。
- 缺失/破损清单（Artifact）：迁移后须补建或修复的自动化。
- 证据为实采事实；模型对清单的归纳为分析，不充当 Evidence。写入 Trace/Audit 并登记 Registry。

## 质量门 / 验收标准
- 每条结论可追溯到具体证据（配置/日志），无「据文档推测」式判断。
- 四态分类（live/broken/redundant/missing）与四类处置（keep/merge/cut/fix）齐备且互斥。
- missing 项（遗留有、迁移后无）必须显式列出，不得遗漏。
- 生产侧变更建议附风险，未经 Gate 不执行。

## 场景要点（按项目场景取用）
- 遗留 Windows 计划任务/Oracle DBMS_SCHEDULER 作业迁到国产 OS/达梦/openGauss/GaussDB 时调度语义不同，须逐项重映射并验证。
- 字符集/时区差异会使迁移后批作业产出与遗留不一致，纳入存活与正确性判定。
- 国产中间件的触发/连接器（消息、ESB）与原栈不一致，redundant/broken 判定须基于目标栈实测。
- 与 P-canary-watch 衔接：上线后自动化的存活纳入灰度观测面。

## 反例 / 禁止
- 禁止凭文档或记忆臆断自动化存在/正常，而无执行证据。
- 禁止本 Skill 直接下线/修改生产定时任务或连接器（须走用户 Gate）。
- 禁止在审计记录中留存凭据、连接串密码、Token。
- 禁止把模型对日志的总结当作 Evidence 归档。
- 禁止遗漏 missing 项只报「现有项正常」。

## 与平台集成
- 与平台 Audit 体系对齐：证据优先、可追溯、不臆断。
- 模型调用经 ModelGateway，用量计入预算；模型输出 ≠ Evidence。
- 盘点结果 → Artifact/Evidence + Trace/Audit + Registry。
- 生产侧变更（cut/fix）走 HITL Gate；与 P-canary-watch 协同观测上线后存活。

## 参考
- ECC skills/automation-audit-ops（MIT, https://github.com/affaan-m/ECC）
- 平台 Audit/Trace 规范
- P-canary-watch（上线后存活观测）
