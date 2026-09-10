# 04-Workspace-Environment-Execution架构

> 文档路径：`文档/02-架构设计/04-Workspace-Environment-Execution架构.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/草稿/04-Workspace-Environment-Execution架构.md`（v0.1）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R2
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中 Project Workspace、Environment Profile、Execution Session 三对象的职责边界、内部结构、生命周期、安全约束、三状态分离（Coding/Execution/Runtime）、后台任务行为，以及与 LangGraph/API/前端的协作关系。本文聚焦于三对象的**架构机制**——状态域定义以 `03-Project-Run-TaskGraph状态架构.md` 为权威源，本文不重复。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-050~D-055）、`文档/02-架构设计/00-架构总纲.md`、`文档/02-架构设计/01-系统分层与技术栈基准.md`、`文档/02-架构设计/03-Project-Run-TaskGraph状态架构.md`。
> 重要边界：本文是架构层设计基准，不替代 API 字段契约、数据库 schema、前端工作区交互规范、ExecutionProvider 实现、部署方案或安全授权规范。本文给出的目录和流程均为 R1 架构建议，R2/R4/R8/R11 需按真实实现校准。
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守以下原则：

```text
1.  每个 Project 必须有独立 Project Workspace（D-050）；
2.  当前版本采用 Project Workspace / Environment Profile / Execution Session 三对象（D-051）；
3.  Project Workspace 负责项目文件持久化；
4.  Environment Profile 负责环境声明化；
5.  Execution Session 负责执行按需隔离；
6.  退出工作区 UI 不等于停止任务（D-052）；
7.  状态恢复由数据库、LangGraph checkpoint、workspace 文件、trace/audit、event log 多源共同承担（D-053）；
8.  容器、终端、前端 UI 不能作为状态源；
9.  Coding State / Execution State / Runtime State 必须分离（D-055）；
10. 所有执行动作必须受 Policy/Hook/Gate/Trace/Audit 约束。
```

本文不得：

```text
1.  把 Workspace 当作唯一状态源；
2.  把 Environment Profile 当作真实运行环境；
3.  把 Execution Session 当作长期持久状态源；
4.  把终端是否存在当作任务是否存在；
5.  把服务预览可打开当作 P5 验证通过；
6.  把文件修改完成当作迁移完成；
7.  把命令执行成功当作交付完成；
8.  绕过 Gate 执行高风险动作；
9.  泄露 Key/Token/Secret/Password（D-032）；
10. 引入 Mission 产品层（D-070）。
```

> 本节"编写原则/本文不得"中的通用红线（每项目独立 Workspace D-050、三对象 D-051、退出 UI 不停任务 D-052、状态源多源 D-053、三状态分离 D-055、预览≠验证通过 D-066、密钥脱敏 D-032、不引入 Mission D-070 等）完整总表见 AGENTS §18 + 01-决策记录；本文仅就地保留与三对象架构职责相关的子集。

---

## 1. 三对象总览

当前版本采用三对象模型：

```text
Project Workspace
  - 项目文件持久化；
  - 保存源码、材料、Artifact、Evidence、运行记录、Trace/Audit 引用；
  - 按 project_id 隔离。

Environment Profile
  - 环境声明化；
  - 描述运行环境、依赖、构建、测试、外部服务和风险；
  - 不等于真实运行容器或进程。

Execution Session
  - 执行按需隔离；
  - 执行命令、构建、测试、Patch、Tool/MCP/外部 Agent 调用；
  - 生命周期可短于 Project Workspace。
```

三者关系：

```text
Project
  ├── Project Workspace：长期文件与材料容器
  ├── Environment Profile：环境声明与执行前置条件
  └── Execution Sessions：按需创建的执行上下文
```

---

## 2. 三对象职责边界

### 2.1 Project Workspace 负责

```text
1.  持久化用户项目源码；
2.  持久化项目材料；
3.  持久化平台生成的 Artifact；
4.  持久化 Evidence 文件或 Evidence 引用；
5.  持久化运行记录、报告、索引；
6.  持久化 Patch、差异、输出文件；
7.  支撑前端代码视图和材料视图（D-048）；
8.  作为状态恢复的来源之一；
9.  按 project_id 隔离文件和产物。
```

### 2.2 Project Workspace 不负责

```text
1.  不作为唯一状态源；
2.  不替代数据库；
3.  不替代 LangGraph checkpoint；
4.  不替代 Execution Session；
5.  不替代权限判断；
6.  不替代 Gate；
7.  不替代 Trace/Audit；
8.  不代表执行环境正在运行。
```

### 2.3 Environment Profile 负责

```text
1.  声明项目运行语言、框架、依赖和版本要求；
2.  声明构建命令和测试命令；
3.  声明启动命令和预览方式；
4.  声明外部服务、数据库、中间件要求；
5.  声明环境变量需求，但不得保存密钥明文；
6.  声明执行风险和前置检查；
7.  为 Execution Session 创建提供输入。
```

### 2.4 Environment Profile 不负责

```text
1.  不保存 Key/Token/Secret/Password 明文；
2.  不表示环境已真实可用；
3.  不代表构建或测试已通过；
4.  不替代运行时健康检查；
5.  不替代 P5 验证 Evidence。
```

### 2.5 Execution Session 负责

```text
1.  执行命令；
2.  执行构建；
3.  执行测试；
4.  生成 Patch；
5.  调用 Tool/MCP/外部 Agent；
6.  启动服务或预览；
7.  收集执行输出；
8.  将结果写回 Workspace 或状态服务；
9.  生成 Trace/Audit；
10. 遇到 Gate 或高风险动作时暂停。
```

### 2.6 Execution Session 不负责

```text
1.  不作为长期状态源；
2.  不替代 Run State；
3.  不替代 Workspace 持久化；
4.  不绕过 Policy/Hook/Gate；
5.  不在未授权时执行高风险动作；
6.  不保存未脱敏密钥；
7.  不把命令执行成功直接标记为验证通过。
```

---

## 3. Project Workspace 架构

### 3.1 Workspace 生命周期

Workspace 生命周期建议包括：

```text
created：已创建；
initialized：已初始化目录；
source_loaded：源码已接入；
indexed：材料和源码已索引；
in_use：被 Run 使用中；
locked：被高风险动作或冲突锁定；
archived：已归档；
corrupted：状态损坏或引用异常；
recovery_required：需要恢复。
```

状态名称为建议，R2/R4 可校准。

### 3.2 Workspace 建议目录结构

建议每个 Project Workspace 采用以下逻辑结构：

```text
workspace/<project_id>/
├── source/                 # 用户源码或源码镜像
├── materials/              # 用户上传材料、项目文档、参考资料
├── artifacts/              # 平台生成产物
├── evidence/               # 验证证据、测试结果、对比结果
├── patches/                # Patch、diff、变更包
├── runs/                   # Run 相关文件型记录
├── logs/                   # 可脱敏执行日志或索引
├── reports/                # 评估、规划、验证、交付报告
├── indexes/                # 文件索引、材料索引、检索索引
└── .rebuild/               # 平台内部元数据，不直接展示给用户
```

说明：

```text
1. 以上为逻辑建议，不是 R1 强制落盘结构；
2. R4/R8 需根据真实工程结构校准；
3. .rebuild/ 不得保存密钥明文；
4. 用户可见材料应通过前端材料视图呈现。
```

### 3.3 Workspace 隔离规则

```text
1. 不同 Project 的 Workspace 必须隔离；
2. 不同 Project 的源码、产物、Evidence、runs 不得混写；
3. Run 可读写所属 Project Workspace；
4. Tool/MCP/外部 Agent 访问 Workspace 必须受权限限制；
5. 高风险写操作必须经过 Gate；
6. 工作区路径不得写死 V26.1（D-002）；
7. 历史 archive 只读参考，不得作为当前 Workspace 运行目录（D-062）。
```

### 3.4 材料身份边界

Workspace 内材料区分身份（详见 `03-版本与目录策略.md`）：

```text
项目文档 | 交接材料 | 产物 | 证据 | 运行记录 | 参考资料
```

核心规则（引用而非复述）：

```text
1. Artifact 不自动晋升为项目文档（D-014）；
2. Evidence 不自动成为验收结论；
3. 参考资料不自动成为当前事实源；
4. 正式项目文档必须经过文档治理和验收流程。
```

前端文件面板应支持代码视图（源码/Patch）和材料视图（文档/交接/产物/证据/参考资料）。

---

## 4. Environment Profile 架构

### 4.1 定位

Environment Profile 是环境声明，不是运行实例。

它回答：

```text
项目需要什么环境；
如何构建；
如何测试；
如何运行；
有哪些外部依赖；
有哪些风险和限制；
执行前需要检查什么。
```

### 4.2 建议结构

Environment Profile 应能表达：

```text
语言与框架栈；
依赖声明（引用）；
构建命令；
测试命令；
运行/启动命令；
预览入口；
外部服务依赖（数据库、中间件等）；
环境变量需求（不保存值）；
密钥需求引用（不保存密钥）；
风险提示；
前置验证要求。
```

具体状态域定义见 `03-Project-Run-TaskGraph状态架构.md`（该文档为状态域权威源）。

### 4.3 Profile Status 生命周期

```text
draft：草案；
detected：系统识别生成；
reviewed：已审核；
approved：可用于执行；
invalid：无效；
outdated：已过期；
superseded：被替代。
```

### 4.4 密钥与环境变量规则

```text
1. Environment Profile 可以声明需要哪些环境变量；
2. 不得保存密钥明文（D-032）；
3. 可以保存 secret_requirements_ref（指向密钥需求描述，不含值）；
4. 可以识别 env、配置文件、密钥文件是否存在；
5. 不得把 Key/Token/Secret/Password 写入报告、日志、Trace、截图、前端、提交或交接材料；
6. 需要密钥时必须通过受控配置或用户授权流程处理。
```

### 4.5 Profile 在 P 阶段间的数据流

Profile 随 P 阶段演进而更新：

```text
P1 建档 → 生成 Profile 草案（detected），含基本语言/框架识别结果；
P2 评估 → 审查 Profile，补充风险提示和缺口，状态→reviewed；
P3 规划 → 将环境准备纳入 Task Plan，Profile 状态→approved；
P4 执行 → 读取 approved Profile 创建 Execution Session；
P5 验证 → 用 Profile 声明的构建/测试/运行方式验证，验证结果反馈到 Profile 的 validation 状态；
P6 交付 → 将最终 Profile 纳入交付说明。
```

关键约束：**P4 执行前 Profile 必须为 approved 状态**；Profile 变更后需重新审核。

---

## 5. Execution Session 架构

### 5.1 定位

Execution Session 是按需创建的执行上下文。

它负责执行命令、构建、测试、Patch、Tool/MCP/外部 Agent 调用。

它不是长期状态源。Session 结束后，关键输出写入 Workspace，状态记录写入 Trace/Audit。

### 5.2 设计约束

Execution Session 的实现必须满足以下最低约束：

```text
1. 必须支持超时自动终止（防止失控进程）；
2. 必须支持外部 kill 信号（用户取消或 Gate 拒绝）；
3. 必须捕获 stdout/stderr 并写入 Trace；
4. 必须限制文件系统访问范围（不超出 Project Workspace + 指定临时区）；
5. 不得要求 root 权限；
6. 密钥不得出现在命令行参数中（通过环境变量或受控配置注入）；
7. Session 结束后必须清理临时资源。
```

具体隔离技术（子进程/容器/sandbox）由 R8/R11 按上述约束选择。

### 5.3 Session Type

```text
shell：命令执行；
build：构建；
test：测试；
patch：生成或应用 Patch；
runtime：启动服务/预览；
tool：Tool 调用；
mcp：MCP 调用；
external_agent：外部 Agent 调用。
```

### 5.4 Session Status 生命周期

```text
created → running → succeeded/failed/canceled/expired
                   → waiting_gate（如遇 Gate）
                   → paused（用户或策略暂停）
```

具体状态枚举见 `03-Project-Run-TaskGraph状态架构.md`。

### 5.5 Session 规则

```text
1. Execution Session 可短生命周期；
2. Session 成功不代表阶段完成；
3. Session 成功不代表 P5 验证通过；
4. Session 输出必须写入 Workspace 或结构化状态；
5. Session 必须产生 Trace；
6. 高风险 Session 必须产生 Audit；
7. Session 失败必须记录原因；
8. Session 不得泄露密钥；
9. Session 结束后，持久状态以 Workspace + 数据库 + Trace/Audit 为准——Session 自身不保留。
```

---

## 6. Coding / Execution / Runtime 三状态分离

这是 D-055 的核心架构要求。三种状态独立持久化、独立审计、互不混淆。

### 6.1 Coding State

表达文件级变化：

```text
文件变更；
编辑历史；
Patch 生成；
产出代码；
代码视图状态。
```

核心验证问题：**代码发生了什么变化？变化来自哪个 Run/Task/Node？是否已生成 Artifact？是否已验证？**

### 6.2 Execution State

表达执行过程：

```text
命令执行；
构建过程；
测试运行；
Patch 应用；
Tool/MCP 调用；
执行输出/失败。
```

核心验证问题：**执行了什么？结果是什么？有没有 Trace？是否触发 Gate？是否产生 Evidence？**

### 6.3 Runtime State

表达运行时实例：

```text
服务进程；
数据库实例；
中间件；
预览端点；
健康状态。
```

核心验证问题：**运行实例是否存在？服务是否可达？是否支撑 P5 验证？**

### 6.4 三状态边界（不可混淆）

```text
1. 文件修改成功 ≠ 构建成功；
2. 构建成功 ≠ 服务可运行；
3. 服务可预览 ≠ P5 验证通过（D-066）；
4. 终端仍在 ≠ 任务仍可信；
5. 容器运行中 ≠ Run 状态 completed；
6. 前端显示成功 ≠ 后端状态成功。
```

---

## 7. 后台任务与退出 UI

### 7.1 退出 UI 规则（D-052）

退出工作区 UI 不等于停止任务。

允许后台继续的情况：

```text
低风险任务；
中风险但已在计划内授权的任务；
无需人工介入的读取、分析、索引、构建或测试；
已通过 Policy/Hook 检查的动作。
```

必须暂停的情况：

```text
遇到 Gate；
写盘越界；
shell 高风险动作；
外部系统写操作；
远程资源写操作；
L5 高风险动作（D-034）；
Policy 冲突；
密钥相关不确定项；
低置信度或范围变化。
```

### 7.2 后台任务状态展示

前端任务面板应展示 Run 状态。具体展示字段以 `03-Project-Run-TaskGraph状态架构.md` §3 Run State 为准，本文不重复定义。

额外要求：

```text
1. 如旧资料中存在 mission_id，R2 必须校准为 run_goal/objective/task_group_id 或删除（D-070）；
2. 不得把 mission_id 作为正式产品主字段直接保留；
3. 退出 UI 时前端应明确告知用户哪些任务将继续后台运行、哪些将暂停。
```

---

## 8. 与 LangGraph 的协作

LangGraph 负责：

```text
Run 主流程；
P0-P6 阶段流转；
Gate interrupt/resume；
checkpoint；
失败和返工分支。
```

Workspace/Environment/Execution 负责：

```text
Workspace：文件、材料、Artifact、Evidence 持久化；
Environment Profile：执行环境声明；
Execution Session：具体命令、构建、测试、Tool/MCP 调用。
```

推荐调用关系：

```text
LangGraph node
  → 读取 Run/Stage/Task 状态
  → 读取 Workspace 引用
  → 读取 Environment Profile（须为 approved）
  → 请求 Execution Session 执行
  → 收集输出
  → 写回 Artifact/Evidence/Trace/Audit
  → 根据结果路由到下一节点或 Gate
```

LangGraph checkpoint 不替代 Workspace 持久化和 Trace/Audit。

---

## 9. 与 API / 前端的协作

### 9.1 API 协作

API 层需要提供或预留：

```text
Project Workspace 查询；
文件树查询；
材料视图查询；
Artifact/Evidence 查询；
Execution Session 状态查询；
Run 状态查询；
Gate 操作；
后台任务操作；
SSE/event log 查询。
```

R1 只给出建议边界，字段进入 `05-API与集成契约/`。

### 9.2 前端协作

前端需要支持：

```text
独立全屏 Project Workspace（D-046）；
代码视图（源码/Patch）；
材料视图（文档/交接/产物/证据/参考资料）；
运行状态展示；
后台任务面板；
Gate 交互；
Evidence/Trace/Audit 右侧检视；
Execution Session 输出查看；
mock/未接真实服务能力显式标记（D-049）。
```

前端不得：

```text
1. 替代后端状态；
2. 替代 Gate 决策记录；
3. 替代 Trace/Audit；
4. 隐藏后台任务真实状态；
5. 把预览成功显示为验证通过。
```

---

## 10. 安全与权限约束

### 10.1 写盘约束

```text
1. 写入 Project Workspace 内部允许按风险级别判断；
2. 写出 Workspace 边界必须提升风险级别；
3. 覆盖用户源码、删除文件、批量修改、Git 操作等需要风险评估；
4. 高风险写盘必须 Gate；
5. 所有写盘必须 Trace。
```

### 10.2 命令约束

```text
1. 命令风险按上下文判断，不硬编码简单白名单（D-033）；
2. 低风险命令可按模式和 Policy 放行；
3. 高风险命令必须 Gate；
4. 命令输出不得泄露密钥；
5. 命令执行失败不得伪装成功。
```

### 10.3 Tool / MCP / 外部 Agent / 资源约束

Workspace/Execution 层面的特有约束：

```text
1. Tool/MCP/外部 Agent 调用必须记录来源、风险、权限、Trace（D-041）；
2. 外部系统写操作必须 Gate；
3. Tool/MCP 的文件系统访问不得超出 Workspace 边界 + 指定临时区。
```

通用资源规则（社区资源默认只读、Case 不得直接执行、确定性转换不享特权）以 `00-架构总纲.md` §6 和 D-040/D-061/D-063 为权威源，本文不重复。

### 10.4 密钥约束

```text
1. 不得在 Workspace、Profile、Session、Trace、Audit、前端、报告、交接中泄露明文密钥（D-032）；
2. 可记录密钥需求，不记录密钥值；
3. 可记录密钥文件是否存在，不展示内容；
4. 发现密钥泄露风险时必须暂停并进入 Gate 或安全处理流程。
```

---

## 11. 状态恢复

状态恢复多源原则和具体恢复流程以 `03-Project-Run-TaskGraph状态架构.md` §15 为权威源。本文仅补充 Workspace/Execution 特有的恢复检查：

Workspace 特有恢复检查：

```text
1. Project Workspace 目录是否存在；
2. Workspace 元数据是否可读；
3. 关键子目录（source/artifacts/evidence）是否完整；
4. 文件索引是否过期（对比 last_indexed_at 与文件系统 mtime）。
```

Execution Session 特有恢复检查：

```text
1. 残留 Session 是否存在（异常退出后清理）；
2. Session 输出是否已完整写入 Workspace；
3. 未完成 Session 是否可安全终止或恢复。
```

如出现冲突，处理流程遵循 `03-Project-Run-TaskGraph状态架构.md` §17.2：停止自动推进→标记 conflict→记录 Trace/Audit→输出冲突说明→请求用户确认。

---

## 12. 典型流程

以下流程描述 Workspace/Environment/Execution 三对象在关键 P 阶段中的协作。

### 12.1 P0 接入

```text
1. 用户创建 Project；
2. 选择接入来源（本地目录/Git/ZIP/GitHub）；
3. 创建 Project Workspace → 状态 created；
4. 接入源码或材料 → 状态 source_loaded；
5. 生成初始 Workspace 索引 → 状态 indexed；
6. 记录接入 Trace；
7. 生成接入 Evidence；
8. 等待进入 P1 的用户 Gate。
```

### 12.2 P1 建档

```text
1. 读取 Workspace 文件树；
2. 识别源码结构、配置和依赖；
3. 生成 Environment Profile 草案 → 状态 detected；
4. 生成项目档案 Artifact；
5. 记录建档 Trace；
6. 生成建档 Evidence；
7. 等待进入 P2 的用户 Gate。
```

### 12.3 P4 执行

```text
1. 读取 Stage Plan / Task Plan / TaskGraph；
2. 校验 Environment Profile 为 approved；
3. 创建 Execution Session → 状态 created；
4. 在 Session 中执行命令/构建/测试/Patch/Tool/MCP；
5. 将输出写回 Workspace（artifacts/evidence/patches/）；
6. 记录 Trace/Audit；
7. 生成 Artifact/Evidence 引用；
8. Session 结束 → 状态 succeeded/failed；
9. 失败或越界时进入 Gate 或返工路径。
```

### 12.4 P5 验证

```text
1. 基于 approved Environment Profile 创建验证 Session；
2. 执行构建验证（build Session）；
3. 执行运行/启动验证（runtime Session）；
4. 执行测试或生成测试（test Session）；
5. 执行回归基线对比；
6. 登记关键行为等价或差异；
7. 所有输出写入 Workspace（evidence/）；
8. 生成验证 Evidence → 状态 collected；
9. 验证不通过 → Stage 进入 rework_required；
10. 验证通过 → Evidence 状态 validated，等待进入 P6 的用户 Gate。
```

P5 不得以"构建成功"或"预览可打开"作为通过依据——必须基于可验证 Evidence（D-066）。

---

## 13. R2 / R4 / R8 / R11 校准项

### 13.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录（含 D-062~D-072）；
2. Workspace 目录结构是否与文档地图和项目目录策略一致；
3. Environment Profile 状态域是否与 03 状态架构一致；
4. Execution Session 状态域是否与 LangGraph/API 文档一致；
5. mission_id 是否仍残留（D-070）；
6. 密钥脱敏规则是否同步到安全文档；
7. P5 验证流程是否与 `08-测试与验收/` 文档一致。
```

### 13.2 R4 工程骨架校准

```text
1. Workspace Service 目录和接口；
2. Environment Profile 数据模型；
3. Execution Session 抽象与实现接口；
4. API 与 SSE 契约；
5. event log 实现；
6. Artifact/Evidence/Trace/Audit 存储索引；
7. SQLite/PostgreSQL 适配边界。
```

### 13.3 R8 工作区真实化校准

```text
1. Project Workspace 文件树真实读写；
2. 代码视图和材料视图真实映射；
3. Artifact/Evidence 展示；
4. 后台任务状态恢复；
5. Gate 与右侧检视联动；
6. mock 能力清理或显式标记。
```

### 13.4 R11 执行链路校准

```text
1. Execution Session 真实命令执行；
2. 构建/测试/Patch 输出采集；
3. Execution Session 隔离机制选型与实现（满足 §5.2 设计约束）；
4. Tool/MCP/外部 Agent 调用边界；
5. Policy/Hook/Gate 集成；
6. Trace/Audit 完整性；
7. 执行失败和返工路径。
```

> R4 校准前应按 D-067 三步法：先立自有方案 → 深读历史版本 workspace/execution 相关代码 → 完善方案后执行。

---

## 14. 架构红线

```text
1.  不得把 Workspace 当作唯一状态源；
2.  不得把 Environment Profile 当作已运行环境；
3.  不得把 Execution Session 当作长期状态源；
4.  不得用终端、容器、前端 UI 判断任务完成；
5.  不得把构建成功等同于迁移完成；
6.  不得把预览可打开等同于 P5 验证通过（D-066）；
7.  不得无 Trace 执行命令；
8.  不得无 Audit 执行高风险动作；
9.  不得泄露密钥（D-032）；
10. 不得执行默认在线社区资源（D-061）；
11. 不得直接执行 Case（D-040）；
12. 不得引入 Mission 产品层（D-070）；
13. Execution Session 不得要求 root 权限。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md（已在各条就地标注 D 编号）；术语与风险级别（L0-L5）详见 02-术语表.md。本文红线为 Workspace/Environment/Execution 主题特有约束，整体保留。

---

## 15. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1.  明确 Project Workspace/Environment Profile/Execution Session 三对象职责与边界（§1~§2）；
2.  明确 Workspace 生命周期、目录建议和隔离规则（§3）；
3.  明确 Environment Profile 声明化边界、密钥规则和 P 阶段数据流（§4）；
4.  明确 Execution Session 类型、设计约束、生命周期和规则（§5）；
5.  明确 Coding/Execution/Runtime 三状态分离及互不混淆边界（§6）；
6.  明确退出 UI 规则——哪些可后台继续、哪些必须暂停（§7）；
7.  明确与 LangGraph、API、前端的协作关系（§8~§9）；
8.  明确安全与权限约束——写盘/命令/密钥/资源（§10）；
9.  明确 Workspace/Execution 特有恢复检查（§11，通用恢复引用 03）；
10. 明确 P0/P1/P4/P5 典型流程中三对象的协作（§12）；
11. 明确 R2/R4/R8/R11 校准项（§13）；
12. 明确架构红线 13 条（§14）；
13. 未固定最终实现 schema；
14. 未引入 Mission 产品层；
15. 未把执行成功、预览成功或前端状态误写为验证通过；
16. 状态域定义以 03 为权威源，本文不重复定义。
```
