---
name: P-codebase-onboarding
description: P0 接入阶段 — 引导接入遗留代码仓库、识别信创迁移源技术栈、构建项目结构与依赖清单并诚实标注未知项
metadata:
  series: P
  phase: P0
  category: stage_skill
  status: platform_runtime
  source: ECC skills/codebase-onboarding (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-codebase-onboarding（代码仓接入与初识）

## 适用阶段与触发条件

- 阶段：P0 接入。每个迁移项目的第一道工序。
- 触发：用户首次提交遗留系统代码仓库（.NET / Java / 混合栈）、或补充新增子系统时。
- 目标：在不修改任何源代码的前提下，建立对遗留系统的可信初识，产出接入档案，为 P1 建档提供事实底座。

## 输入

- 遗留代码仓库（只读挂载或快照），可能含 .sln / pom.xml / build.gradle / *.csproj / web.config / app.config。
- 用户口述的系统背景（业务域、上线年份、是否仍在运行、已知痛点）。
- 现有部署拓扑 / 数据库连接串（若提供，仅读取结构，禁止落盘明文凭据）。

## 执行步骤

四阶段渐进式分析，每阶段产出可挂载的中间事实，禁止臆测填充：

1. **侦察（Recon）**：扫描仓库根，列出顶层目录、解决方案/模块文件、入口程序（Program.cs / Main / Startup / web.xml）。统计语言分布与代码量（按扩展名）。识别 monorepo / 多项目结构。
2. **技术栈识别（Stack）**：解析项目文件确定运行时与版本——.NET Framework 版本（4.x vs .NET Core/5+）、Java 版本（JDK 8/11/17）、构建工具、Web 框架（ASP.NET WebForms/MVC/WCF、Spring/Struts）、ORM（EF/Hibernate/MyBatis）、数据库类型（Oracle/MSSQL/MySQL）、中间件（IIS、WebLogic、Tomcat、WebSphere）。
3. **约定与依赖识别（Convention）**：提取 NuGet / Maven / Gradle 依赖清单及版本；标出已知不兼容信创目标的依赖（Windows-only API、闭源驱动、商业组件）；识别配置约定、命名约定、分层结构。
4. **产物固化（Artifacts）**：生成接入档案与项目级 onboarding 说明，所有未能确定的项以 `【未知-需用户确认】` 显式标注，绝不猜测版本或行为。

## 输出 / 产物（Artifact / Evidence）

- 《接入档案 / Onboarding Guide》：技术栈矩阵、模块清单、依赖清单、入口与拓扑。
- 项目级 onboarding 摘要（可作为后续 Agent 冷启动 brief 的事实源）。
- 依赖清单（结构化），标注信创兼容性初判（兼容 / 需替换 / 待查）。
- 「未知项清单」：所有需用户 / 官方文档确认的条目。
- 全部产物挂 Project/Stage，附 Trace（扫描动作）与 Audit；扫描结果即 Evidence。

## 质量门 / 验收标准

- 技术栈每一项都有来源证据（来自哪个文件哪一行 / 哪个依赖声明），不得仅凭经验断言。
- 依赖清单覆盖率：可解析的项目文件 100% 被解析；无法解析的显式列出原因。
- 「未知项清单」非空即合格信号——空清单往往意味着臆测，需复核。
- 未对源仓库做任何写操作（git 工作区干净）。

## 场景要点（按项目场景取用）

- .NET Framework 4.x 与 .NET Core/5+ 接入判断截然不同：前者大概率需重写（System.Web / WCF / WebForms 在麒麟 / 统信上不可用），后者多为适配。务必区分。
- Oracle/MSSQL 连接串、专有驱动（System.Data.OracleClient、Oracle.DataAccess）须标记为「目标栈需替换为达梦 DM / openGauss / GaussDB 驱动」。
- 识别 Windows 强耦合点：注册表、COM/DCOM、Windows 服务、IIS 专有模块、路径分隔符——这些是迁移麒麟 / 统信的高风险项，P0 即标注。
- 中间件识别要落到具体版本：WebLogic→东方通 TongWeb、WebSphere→宝兰德，版本差异决定迁移工作量。
- 信创栈训练数据稀缺，遇到国产组件版本 / 行为不确定时一律进「未知项清单」，留待 P-documentation-lookup 查证。

## 反例 / 禁止

- 禁止凭仓库「看起来像」就断定框架版本——必须有声明文件证据。
- 禁止修改、格式化、重命名源仓库任何文件。
- 禁止在档案 / 日志中记录明文数据库密码、连接凭据、Key/Token/Secret。
- 禁止把「未知」写成「无」或留空假装完整。
- 禁止把社区 / 案例资源中的代码当作可执行迁移产物直接引用（Case 资源永不可执行）。

## 与平台集成

- 任何需要模型辅助的分析（如依赖兼容性初判摘要）必须经 ModelGateway 调用，不直接调 Provider。
- P0 全为只读分析，不涉及 L4-L5 外部写操作；若需拉取外部依赖元数据等网络写操作，按 Gate 策略请求用户授权。
- 所有档案产物挂 Project/Run/Stage/Node，生成 Artifact/Evidence 并附 Trace/Audit/Registry 登记。
- 模型对仓库的判断属辅助，不是 Evidence；Evidence 必须是扫描 / 解析的客观结果。
- 本 Skill 自验证（如清单完整性自检）不替代后续 Acceptance 验收。
- 引用的社区 Skill 默认只读。

## 参考

- ECC `skills/codebase-onboarding`（MIT, https://github.com/affaan-m/ECC）
- rebuild 文档：`文档/00-项目治理/`（阶段定义）、`文档/04-模型与资源/`（资源接入与 Trace/Audit 规范）
