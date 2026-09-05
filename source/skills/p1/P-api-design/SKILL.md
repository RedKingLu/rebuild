---
name: P-api-design
description: P1 建档 / P4 执行 — 分析遗留系统接口契约并建档，并为迁移后重建服务提供目标 API 设计规范
metadata:
  series: P
  phase: P1
  category: documentation_skill
  status: platform_runtime
  source: ECC skills/api-design (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---
> **场景适用性说明**：本文档中的具体技术栈举例（国产化数据库 / OS / CPU、中间件替换候选等）**以信创切换场景为例**——它是平台典型场景**之一**，不是唯一场景。请以本项目实际的场景包（`source/skills/scenarios/<scenario>/`）与 `migration_target` 为准；**本文举例不得无条件套用**。

# P-api-design（API 契约分析与设计）

## 适用阶段与触发条件

- 阶段：P1 建档（分析遗留接口、建档、识别迁移依赖）；P4 执行（迁移后重建服务时作为目标 API 设计参考）。
- 触发：遗留系统含 API/服务层（REST / WCF / SOAP / gRPC / GraphQL），或迁移目标需重新暴露服务接口。
- 目标：P1 把现有接口契约固化为可追溯的档案；P4 在重建时遵循一致的目标 API 规范，避免迁移引入接口语义漂移。

## 输入

- 遗留服务代码（Controller / WebMethod / WCF ServiceContract / Spring @RestController）。
- 现有接口文档、WSDL、Swagger/OpenAPI（若有）。
- P0 接入档案与依赖清单；调用方清单（前端 / 其他系统）。

## 执行步骤

1. **P1 接口盘点**：扫描并列出全部接口——路径、HTTP 方法、参数、请求/响应体、状态码、鉴权方式、版本。区分内部接口与对外契约。
2. **依赖与调用链识别**：还原调用关系（前端→网关→服务→服务），标注哪些接口有外部消费方（契约不可随意变更）、哪些为内部可重构。
3. **契约建档**：以结构化形式（建议 OpenAPI 3.x）记录现状，原样保留语义，不在 P1 阶段「顺手优化」。
4. **P4 目标设计参考**：重建时遵循 REST 规范——资源用复数名词 + kebab-case URL（`/migration-tasks`）、方法语义正确（GET 幂等读、POST 创建、PUT/PATCH 更新、DELETE 删除）、状态码语义化（2xx/4xx/5xx 区分客户端与服务端错误）、统一错误响应体（code/message/traceId）、分页与过滤约定、显式版本（`/v1/`）、必要时限流。
5. **迁移依赖标注**：标出每个接口的迁移优先级与风险（强耦合 Windows/Oracle 的接口高风险）。

## 输出 / 产物（Artifact / Evidence）

- 遗留接口清单与 OpenAPI 契约档案。
- 接口依赖图（含外部消费方标注）。
- 迁移优先级与风险标注表。
- P4 阶段：目标 API 设计规范文档 + 与遗留契约的差异对照。
- 产物挂 Project/Stage/Node，附 Trace/Audit；扫描出的接口定义即 Evidence。

## 质量门 / 验收标准

- 接口清单覆盖全部可达接口；未覆盖项注明原因。
- 每个对外契约标注是否有外部消费方，变更影响可评估。
- P4 目标设计通过 REST 规范自检（命名 / 方法 / 状态码 / 错误体 / 版本一致）。
- 契约差异对照可追溯到具体接口。

## 场景要点（按项目场景取用）

- WCF / .asmx (SOAP) 在 .NET Core 与国产中间件上无对等实现，须规划改为 REST/gRPC，契约语义须 1:1 映射，P1 即记录原 WSDL 语义。
- 接口若直接拼 Oracle/MSSQL 方言 SQL 或返回数据库专有类型（如 Oracle DATE 精度），迁移到达梦 DM / openGauss / GaussDB 后行为可能变化，须在契约档案中标注数据类型与精度约定。
- 依赖 Windows 集成认证（NTLM/Kerberos via IIS）的接口，迁移麒麟 / 统信后鉴权方式需重设计，列入高风险。
- 国产中间件（东方通 TongWeb / 宝兰德）对长连接、流式、超时的默认行为与 IIS/WebLogic 不同，目标 API 设计须明确超时与重试约定。

## 反例 / 禁止

- 禁止在 P1 建档时擅自「改进」现有契约——建档只记录现状。
- 禁止虚构未在代码中实际存在的接口或参数。
- 禁止把 API Key / 鉴权 Token 写入档案示例。
- 禁止 P4 目标设计破坏外部消费方依赖的对外契约而不经 Gate 评审。

## 与平台集成

- 接口语义归纳、OpenAPI 草拟等模型辅助必须经 ModelGateway，不直接调 Provider。
- P4 若需对外部系统发起探测 / 重建部署等 L4-L5 写操作，须经用户 Gate 授权。
- 契约档案、依赖图挂 Project/Run/Stage/Node，生成 Artifact/Evidence 并附 Trace/Audit/Registry。
- 模型生成的接口描述属辅助说明，不是 Evidence；Evidence 是源码中的接口定义本身。
- 自检不替代 Acceptance 验收；引用的社区 Skill 默认只读。

## 参考

- ECC `skills/api-design`（MIT, https://github.com/affaan-m/ECC）
- rebuild 文档：`文档/05-API与集成契约/`、`文档/00-项目治理/`
