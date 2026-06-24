# 03-ProjectWorkspace布局规范

> 文档路径：`文档/06-UX与前端/03-ProjectWorkspace布局规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.1.1
> 来源草稿：`产物/草稿/03-ProjectWorkspace布局规范.md`（Copilot v0.1，2026-06-23）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R1 文档正式化流程
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本 Project Workspace 的前端布局规范——覆盖独立全屏 IDE 心智（D-046）、七区布局（活动图标条/左侧面板/顶部状态栏/Gate中央横幅/中央类型单例Tab/底部Dock/右侧检视）、VSCode式活动图标条、4种核心Tab类型（D-047）、代码与材料双视图（D-048）、Agent 对话常驻（D-047）、终端/输出/任务进度 Dock、Gate/Evidence/Trace/Audit 检视、SSE 实时刷新、断线恢复、退出≠停止任务（D-052）、响应式策略、安全脱敏、mock 边界，以及 R2/R3/R4/R8/R9-R12 校准项。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-001~D-010, D-023~D-034, D-045~D-055, D-066, D-068, D-070）、`文档/06-UX与前端/00-UX与前端总览.md`（§9-§13, §18-§19）、`文档/06-UX与前端/01-信息架构与项目创建.md`（§15）、`文档/06-UX与前端/02-项目概览与后台任务.md`（§4, §13, §17）、`文档/05-API与集成契约/07-SSE与事件契约.md`
> 重要边界：本文是 UX 与前端专题下的 Project Workspace 布局规范，不替代产品定义、架构设计、API 契约、Workspace Service 实现、前端视觉系统、组件规范、状态管理实现或前端代码。本文为 R1 正式候选契约，R2/R3/R4/R8/R9-R12 需按真实实现和联调结果校准。
> 修订说明：R2 校准（Q4=A）：UX 设计内容旧 F 命名统一为 P0-P6；R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留

---

## 0. 编写原则

本文遵守当前文档事实源层级（D-068）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开 Project Workspace 布局规范，不重新定义上级事实。

### 0.1 本文必须遵守

```text
1.  Workspace 采用独立全屏 IDE 心智（D-046）
2.  Workspace 不作为普通主导航页面常驻（D-046）
3.  Workspace 应从 Project 或概览进入，并提供返回平台入口（D-046）
4.  每个用户项目必须有独立 Project Workspace（D-050）
5.  文件、产物、Evidence、runs、任务状态按 project_id 隔离（D-050）
6.  中央区域采用类型单例 Tab（D-047）
7.  Agent 对话 Tab 常驻不可关闭（D-047）
8.  阶段页、文件编辑/预览、Artifact 预览等按类型单例替换（D-047）
9.  终端、输出、问题、任务进度在底部 Dock
10. Evidence、Trace、Audit 在右侧检视
11. 文件面板支持代码视图与材料视图（D-048）
12. 退出工作区 UI 不等于停止任务（D-052）
13. 状态恢复不能依赖容器、终端或前端 UI（D-053）
14. Gate、Evidence 缺口、Trace/Audit 缺失必须可见（D-023, D-066）
15. mock 必须显式标记（D-049）
16. 不得泄露 Key/Token/Secret/Password（AGENTS.md §12）
```

### 0.2 本文不得

```text
1.  把 Workspace 做成普通主导航页常驻（D-046）
2.  把 Workspace UI 当作状态源（D-053）
3.  把终端输出当作状态源（D-053）
4.  把退出 Workspace 解释为停止任务（D-052）
5.  隐藏 Active Gate（D-023）
6.  隐藏 Evidence 缺口（D-066）
7.  隐藏 Trace/Audit 缺失
8.  把 Artifact 自动显示为 Evidence
9.  把 Evidence candidate 显示为 Evidence validated（D-066）
10. 将 mock Workspace 显示为真实可用（D-049）
11. 引入 Mission 产品层（D-070）
12. 使用旧 Phase/旧 F0-F6/旧 S0-S7 作为当前主流程
13. 在长期前端路由、组件、状态字段中固化 V26.1（D-002）
```

---

## 1. Project Workspace 定位

Project Workspace 是单个 Project 的独立工作场所，用于承载源码、产物、证据、运行记录、任务进度、Agent 协作、执行输出与 Gate 决策。

Workspace 应回答：

```text
1.  当前打开的是哪个 Project
2.  当前 Project 是否有运行中的 Run
3.  当前处于 P0-P6 哪个阶段
4.  是否存在 Active Gate
5.  哪些文件属于源码、产出代码、Patch
6.  哪些材料属于项目文档、交接材料、产物、证据、运行记录、参考资料
7.  当前 Agent 在做什么
8.  执行输出在哪里看
9.  Evidence、Trace、Audit 在哪里看
10. 离开 Workspace 后任务是否仍在后台运行
```

Workspace 不是：

```text
1. 唯一状态源
2. 终端模拟器集合
3. 普通文件管理器
4. 纯 Agent 聊天页
5. 内部 R 阶段看板
6. Mission 工作台（D-070）
```

> 关联决策：D-046（全屏 IDE 心智）、D-050（独立 Workspace）、D-070（不设 Mission）

---

## 2. 总体布局

R1 正式候选 Workspace 采用七区布局：

```text
活动图标条：  VSCode 式 6 项切换（阶段/文件/Git/远程/搜索/Agent），50px 宽固定
左侧面板：    随活动图标切换内容（FlowRail/文件树/Git变更/远程主机/搜索/Agent命中）
顶部：        Project / Run / Stage / Gate / 执行模式 / 后台任务状态栏
Gate 横幅：   Active Gate 时在中央编辑区顶部常驻（D-023）
中央：        类型单例 Tab 工作区（D-047）
底部：        Dock，承载终端、输出、问题、任务进度（默认折叠，可拖拽高度）
右侧：        Evidence / Trace / Audit / Context / Resource / Model 检视（可折叠为竖条）
全局：        返回平台入口和后台任务入口
```

布局原则：

```text
1. 活动图标条用于切换左侧面板内容，点击当前图标再次=折叠面板（VSCode 行为）
2. 左侧面板用于定位文件/阶段/Git/远程/Agent 能力
3. 顶部用于建立当前上下文和快捷操作
4. Gate 横幅用于阻断提示，用户决策前不可消除
5. 中央用于主工作内容
6. 底部用于执行与运行输出
7. 右侧用于可信交付依据和能力状态
8. Gate、Evidence 缺口和后台任务必须跨区域可见（D-023, D-066）
```

> 关联决策：D-046, D-047, D-048

---

## 3. 活动图标条（Activity Bar）

活动图标条是 Workspace 的第一交互入口，位于最左侧 50px 固定宽度区域。采用 VSCode 式切换行为：点击图标展开对应左侧面板，再次点击当前图标折叠面板。

### 3.1 活动项（6 项）

```text
1. 阶段（FlowRail） — 默认展开。显示 P0-P6 阶段列表，点击阶段在中央打开阶段页 Tab（D-009）
2. 文件            — 显示文件树（代码视图：产出物/产出代码/源代码）
3. Git             — 显示当前 Git 变更、分支、提交信息输入
4. 远程            — 显示接入的远程主机/Docker/SSH 列表
5. 搜索            — 工作区内文件与材料检索
6. Agent           — Agent 计划、当前命中能力、等待确认事项
```

### 3.2 面板切换按钮

活动图标条底部设有两个面板切换按钮：

```text
右侧面板切换 — 展开/折叠右侧检视面板
底部面板切换 — 展开/折叠底部 Dock
```

### 3.3 活动条行为规则

```text
1. 点击活动图标 → 左侧面板展开对应内容，图标高亮为 active 态
2. 再次点击当前 active 图标 → 左侧面板折叠
3. 点击不同图标 → 左侧面板切换内容，面板保持展开
4. 左侧面板宽度 264px，折叠后完全隐藏
5. 面板开关状态属于 ui_state，不得影响业务状态（D-053）
```

---

## 4. 顶部状态栏

顶部状态栏建议交互元素（从左到右）：

```text
返回平台按钮
Project 名称 + 模板标签
P0-P6 迷你进度条（彩色圆点，当前阶段高亮，D-009）
当前阶段名称 + 执行会话状态指示
Active Gate 指示（橙/红色圆点 + "待决策"标签）
执行模式切换（Auto / Plan-Confirm / Manual，三按钮，D-025）
工作区信息按钮（点击打开三对象模型弹窗，D-051）
后台任务徽章（显示运行中/等待 Gate/失败任务数，D-054）
主题切换按钮
```

展示规则：

```text
1. P0-P6 迷你进度条颜色：灰色=未启用/已跳过、蓝色=当前进行中、绿色=已完成、琥珀色=等待Gate、红色=阻塞
2. current_stage 和 run_status 来自后端
3. Active Gate 指示不得隐藏，存在时必须醒目（D-023）
4. 执行模式切换：Auto=自动执行 / Plan-Confirm=计划确认 / Manual=手动（D-025）
5. 工作区信息弹窗展示三对象：ProjectWorkspace / EnvironmentProfile / ExecutionSession（D-051）
6. 后台任务徽章可点击打开后台任务面板（D-054）
7. 顶部状态栏不得显示 mission_id（D-070）
8. 顶部状态栏不显示密钥、Token 或敏感路径
```

> 关联决策：D-023, D-053, D-066, D-070

---

## 5. 返回平台入口

Workspace 必须提供明确的返回平台入口（D-046）。

建议入口：

```text
返回项目概览    → 02-项目概览与后台任务
返回项目列表    → 01-信息架构与项目创建 §5
返回平台概览    → 00-UX与前端总览 §4
```

交互规则：

```text
1. 退出 Workspace 不等于停止任务（D-052）
2. 如果存在运行中任务，应提示任务将按后端规则继续或暂停
3. 如果存在 Active Gate，应提示需要处理 Gate（D-023）
4. 如果任务正在执行高风险动作或等待授权，应展示阻塞原因
5. 不得用关闭 Tab 作为取消任务动作
```

> 关联决策：D-023, D-046, D-052

---

## 6. 左侧面板总览

左侧面板内容随活动图标条切换（§3），支持 6 种面板视图：

```text
阶段 FlowRail   — P0-P6 阶段列表，点击阶段在中央打开阶段页 Tab（D-009）
文件树          — 代码视图：产出物/产出代码/源代码（初始三根目录形态）
                 （材料视图：R4/R8 扩展后支持项目文档/交接材料/产物/证据/运行记录/参考资料，D-048）
Git 变更        — 当前更改文件列表、分支信息、提交信息输入
远程资源        — 接入的远程主机/Docker/SSH 列表
搜索            — 工作区内文件与材料检索
Agent 命中      — Agent 当前计划、命中 Skill/Rule/Context/Hook·Policy 检查
```

面板规则：

```text
1. 阶段 FlowRail 默认展开，活动图标 1 默认 active
2. 文件树初始采用三根目录（产出物只读/产出代码可编辑/源代码只读），配 EV/AR/CH 标记
3. 材料视图（6 类材料身份）为 R4/R8 扩展能力，详见 04-文件面板与材料视图.md
4. Git/远程/搜索面板在 V26.1 初期可为占位 mock（D-049），R4-R8 逐项真实化
5. Agent 命中面板展示引用摘要，不展示不可控内部推理
6. 历史参考资料必须标记只读参考
7. Artifact 不自动晋升为项目文档
8. Evidence candidate 不自动显示为 validated（D-066）
9. 密钥文件只显示存在与脱敏状态，不显示明文
```

> 关联决策：D-048, D-066

---

## 6. 代码视图

代码视图建议展示：

```text
源码目录
产出代码目录
Patch 目录
Diff 入口
变更文件标记
执行相关文件标记
测试文件标记
```

交互建议：

```text
打开文件
预览文件
编辑文件
查看 Diff
查看 Patch
查看执行输出
定位到 Artifact / Evidence
```

规则：

```text
1. 文件编辑必须区分草稿和已保存
2. 写盘动作按风险和权限处理（D-034, L0-L5）
3. 高风险写盘必须 Gate（D-034）
4. 代码视图不得显示密钥明文
5. 前端不得根据文件变化自行判断阶段完成（D-053）
```

> 关联决策：D-034, D-053

---

## 7. 材料视图

材料视图建议分类：

```text
项目文档     — 长期事实源
交接材料     — 协作区材料
产物         — Artifact
证据         — Evidence
运行记录     — Trace / Audit / Event / Run 记录
参考资料     — 只读参考，不自动进入当前事实源
```

分类规则：

```text
1. 项目文档是长期事实源的一部分
2. 交接材料是协作区材料
3. 产物是 Artifact
4. 证据是 Evidence
5. 运行记录包含 Trace/Audit/Event/Run 记录
6. 参考资料只读参考，不自动进入当前事实源
```

展示规则：

```text
1. 材料身份必须明确
2. Artifact 不自动成为项目文档
3. Evidence 必须显示验证状态（D-066）
4. Trace/Audit 默认显示摘要和引用
5. 参考资料必须标记只读
6. 敏感内容必须脱敏
```

> 关联决策：D-066

---

## 8. Gate 中央横幅

Active Gate 时在中央编辑区顶部常驻横幅（位于 Tab bar 上方），是 Gate 的前端主展示位。

### 8.1 横幅内容

```text
Gate 标题（如"P3→P4 方案确认 Gate"）
摘要（如"方案已生成，请确认后进入执行阶段"）
风险级别（L0-L5 标签）
风险提示（关联 patch/写盘/shell 等高风险动作时重点列出）
操作按钮：确认/批准  |  返回修改/拒绝
查看详情（展开右侧 Gate 检视）
```

### 8.2 横幅规则

```text
1. Active Gate 存在时常驻，黄色/橙色底，不可折叠或关闭（D-023）
2. 用户决策（批准/拒绝/要求补充）后横幅消失，状态从后端刷新
3. Policy blocked 时横幅为红色阻断态，不显示"批准"按钮（D-030）
4. L5 高风险动作时横幅明确提示"需用户 Gate 确认"（D-034）
5. 横幅仅展示当前 Active Gate 摘要，详细字段和决策选项在右侧检视 Gate 面板
6. 横幅 UI 状态不替代后端 Gate 事实（D-053）
```

> 关联决策：D-023, D-030, D-034, D-053

---

## 9. 中央类型单例 Tab 总览

中央区域采用类型单例 Tab（D-047）。核心 4 种：

```text
Agent 对话            — 常驻不可关闭（D-047）
阶段页                — P0-P6 阶段视图，单例替换
文件编辑/预览         — 代码文件查看与编辑，单例替换
Artifact 预览         — 产物查看，单例替换（列表 ↔ 预览）
```

后续阶段扩展（R8-R12 按需追加）：

```text
TaskGraph             — 任务图与节点状态
Diff / Patch          — 变更查看
报告预览              — 报告/分析结果
```

类型单例规则：

```text
1. Agent 对话 Tab 常驻不可关闭（D-047）
2. 同类型 Tab 默认替换，不无限堆叠（D-047）
3. 可编辑文件有未保存变更时，切换文件弹出"保存并切换 / 放弃并切换 / 取消"
4. Artifact 与 Evidence 应区分身份
5. Gate 决策不在独立 Tab 中进行——Gate 通过中央横幅 + 右侧检视处理
6. 高风险操作不得仅通过关闭 Tab 取消
```

> 关联决策：D-047

---

## 10. Agent 对话 Tab

Agent 对话 Tab 是 Workspace 中常驻不可关闭的协作入口（D-047）。

Agent 对话应支持：

```text
解释当前 Project 状态
解释当前阶段
解释 Active Gate
解释 Evidence 缺口
触发计划讨论
展示下一步建议
请求用户补充信息
引导用户查看 Artifact / Evidence / Trace / Audit
```

规则：

```text
1. Agent 对话不得代替 Gate 决策（D-023）
2. Agent 对话不得批准 Policy 禁止项
3. Agent 输出不得自动成为 Evidence
4. Agent 输出不得自动成为项目文档
5. Agent 不得展示密钥明文（AGENTS.md §12）
6. Agent 对话必须能引用后端事实，而非前端猜测（D-053）
```

> 关联决策：D-023, D-047, D-053

---

## 11. 阶段页 Tab

阶段页 Tab 用于展示当前 P0-P6 阶段内容（与 `00-UX与前端总览.md` §7 一致）。

建议展示：

```text
stage
stage_status
stage_goal
Stage Plan
Task Plan
TaskGraph
Artifact
Evidence
Evidence Gap
Gate
Trace / Audit
next_actions
```

规则：

```text
1. stage 必须是 P0-P6（D-009）
2. 被裁剪阶段不得显示 completed（D-009）
3. 阶段晋级 Gate 必须用户授权（D-023）
4. Evidence 不足不得显示 completed（D-066）
5. Artifact 数量不得等同于完成度
6. 阶段页不得使用旧 Phase/F0-F6/S0-S7
```

> 关联决策：D-009, D-023, D-066

---

## 12. TaskGraph Tab

TaskGraph Tab 用于展示任务图、节点状态、边流转和阻塞。

建议展示：

```text
task_graph_id
TaskGraph 状态
Node 列表
Edge 路由
NodeLoop 状态摘要
当前运行 Node
等待 Gate 的 Node
失败/返工 Node
Artifact / Evidence 输出
Trace 引用
```

规则：

```text
1. TaskGraph UI 不替代 LangGraph checkpoint（D-037）
2. Node completed 不等于 P5 验证通过（D-066）
3. Edge 事件不得隐藏 gate_policy / failure_policy
4. NodeLoop 只展示摘要，不把内部循环做成产品阶段
5. TaskGraph 状态来自后端
```

> 关联决策：D-037（LangGraph 主编排，不替代）

---

## 13. 文件编辑 / 预览 Tab

文件编辑/预览 Tab 应区分编辑状态和只读状态。

建议状态：

```text
preview
editing
dirty
saving
saved
save_failed
read_only
blocked_by_gate
```

规则：

```text
1. read_only 文件不得编辑
2. 历史参考资料默认只读
3. 写盘动作需遵循风险和权限（D-034）
4. 高风险写盘必须 Gate（D-034）
5. save_failed 必须显示 error_ref
6. 文件内容不得泄露密钥
```

> 关联决策：D-034

---

## 14. Artifact / Evidence Tab

Artifact 预览和 Evidence 详情应明确区分（与 `00-UX与前端总览.md` §15 一致）。

### Artifact Tab

```text
artifact_id
artifact_type
artifact_status
来源
关联 stage / node
预览
是否 Evidence 候选
Trace 引用
```

### Evidence Tab

```text
evidence_id
evidence_type
evidence_status
validation_status
claim_refs
来源
限制说明
Evidence Gap
Trace / Audit 引用
```

规则：

```text
1. Artifact 不自动显示为 Evidence
2. Evidence candidate 不自动显示为 validated（D-066）
3. Evidence validated 必须来自后端状态
4. Evidence 缺口必须展示（D-066）
5. 模型输出和资源输出不得自动成为 Evidence validated
```

> 关联决策：D-066

---

## 15. Diff / Patch Tab

Diff/Patch Tab 用于展示代码变更。

建议展示：

```text
patch_id
关联 run_id / node_id
变更文件
Diff
风险级别
生成来源
应用状态
Gate
Trace / Audit
```

规则：

```text
1. Patch generated 不等于 Patch applied
2. Patch applied 不等于 P5 验证通过（D-066）
3. 高风险 Patch 应 Gate（D-034）
4. Patch 应能关联 Artifact
5. Patch 预览不得泄露密钥
```

> 关联决策：D-034, D-066

---

## 16. 底部 Dock 总览

底部 Dock 建议承载：

```text
终端
输出
问题
任务进度
执行会话
测试结果
构建结果
```

Dock 规则：

```text
1. Dock 可以折叠，但关键阻塞不得隐藏
2. 终端输出不是状态源（D-053）
3. 执行输出不得泄露密钥
4. shell/写盘/外部系统写操作遇 Gate 必须暂停（D-034, D-052）
5. 执行失败必须显示 error_ref / trace_ref
6. 输出内容可以使用 output_ref 按需查看
```

> 关联决策：D-034, D-052, D-053

---

## 17. 终端与输出面板

终端与输出面板用于查看执行过程，但不能作为状态源（D-053）。

建议展示：

```text
execution_session_id
command_summary
execution_state
runtime_state
output_ref
exit_code
error_ref
trace_ref
audit_ref
gate_ref
```

规则：

```text
1. 终端输出默认不显示敏感内容
2. command_summary 应脱敏
3. 高风险命令必须 Gate（D-034）
4. external_write 必须 Gate/Audit（D-034）
5. exit_code 不等于业务完成
6. 终端关闭不等于任务取消
```

> 关联决策：D-034, D-053

---

## 18. 任务进度面板

任务进度面板用于展示 Run/TaskGraph/Node/Execution Session 的当前进度。

建议展示：

```text
run_id
run_status
current_stage
task_graph_id
current_node_id
node_status
active_gate
progress_summary
next_actions
```

规则：

```text
1. 进度来自后端
2. progress_summary 只是摘要
3. 进度条不得推断 completed（D-053）
4. waiting_gate 必须高亮（D-023）
5. failed 必须显示错误详情入口
6. resync_required 必须触发刷新
```

> 关联决策：D-023, D-053

---

## 19. 右侧检视总览

右侧检视承载可信交付与能力状态信息（与 `00-UX与前端总览.md` §13 一致）。

建议检视（6 类，含 Gate 决策详情）：

```text
Gate      — 当前 Gate 决策详情与历史 Gate 列表（主展位在中央横幅 §8）
Evidence  — 证据链、证据状态、证据缺口
Trace     — 运行过程、状态变化、模型调用、资源调用
Audit     — Gate 决策审计、高风险动作审计
Context   — Agent 当前引用的上下文摘要
Resource  — 当前调用的资源状态（Tool/MCP/Expert Agent/Skill）
Model     — 当前使用的模型 Profile 与调用状态
```

优先级规则：

```text
1. blocking Evidence Gap 优先（D-066）
2. 高风险 Audit 优先（D-034）
3. Active Gate 详情优先（D-023，主展位在中央横幅 §8）
4. 执行失败 Trace 优先
5. 普通 Context/Resource/Model 信息次之
```

折叠行为：

```text
1. 右侧面板可折叠为 36px 竖条，显示"证据 · 过程 · 审计"文字
2. 点击竖条或活动图标条的右面板切换按钮展开
3. 折叠不得隐藏 Active Gate 指示（D-023）——Gate 指示在中央横幅和顶栏始终可见
```

> 关联决策：D-023, D-034, D-066

---

## 20. Gate 检视

Gate 的前端展示采用双层结构：**中央横幅（主展位）+ 右侧检视（详情）**。

### 20.1 中央横幅（主展位，§8）

Active Gate 时在中央编辑区顶部常驻，用户决策前不可关闭。展示标题、摘要、风险级别、操作按钮（批准/拒绝/要求补充信息/返回修改）。

### 20.2 右侧 Gate 检视（详情 + 历史）

右侧 Gate 检视用于展示：

```text
gate_id / gate_type / gate_status
reason / risk_level
关联 run / stage / node
关联 Artifact / Evidence / Evidence Gap
完整决策选项列表（options / recommended_option）
trace_ref / audit_ref
历史 Gate 列表（已决策 + 已过期）
```

规则：

```text
1. Gate 主展位为中央横幅（§8），不可只显示在右侧检视中
2. 右侧 Gate 检视提供完整字段详情与历史 Gate 列表
3. Gate 不得隐藏（D-023）
4. Gate 不得只显示为 toast（D-023）
5. P 阶段晋级 Gate 必须用户授权（D-023）
6. L5 高风险动作必须用户 Gate（D-034）
7. Policy 禁止项不得可批准（D-030）
8. Gate 决策必须写 Audit
```

> 关联决策：D-023~D-034

---

## 21. Evidence 检视

Evidence 检视用于展示证据链和缺口（D-066）。

建议展示：

```text
Evidence 列表
Evidence 状态
validation_status
claim_refs
Evidence Gap
blocking 标记
来源 Artifact
Trace / Audit 引用
```

规则：

```text
1. Evidence Gap 必须可见（D-066）
2. blocking 缺口必须突出
3. Evidence candidate 不得显示为 validated（D-066）
4. Evidence 缺失不得显示 completed（D-066）
5. P5 验证必须基于可验证 Evidence（D-066）
```

> 关联决策：D-066

---

## 22. Trace / Audit 检视

Trace/Audit 检视用于展示运行记录和审计记录。

### Trace 建议展示

```text
trace_id
trace_type
summary
关联 run / stage / node
created_at
```

### Audit 建议展示

```text
audit_id
audit_type
risk_level
关联 gate_id
summary
created_at
```

规则：

```text
1. Trace 不等于 Evidence
2. Audit 不等于用户授权本身
3. Gate 决策必须能看到 audit_ref（D-023）
4. 高风险动作必须能看到 Audit（D-034）
5. Trace/Audit 详情按权限展示
6. 敏感内容必须脱敏
```

> 关联决策：D-023, D-034

---

## 23. Context / Resource / Model 检视

Context/Resource/Model 检视用于帮助用户理解当前 Agent 的输入与能力调用。

建议展示：

```text
Context 引用
Resource 调用
Tool / MCP / Expert Agent
Case / Knowledge 只读引用
Model Profile
Model Call 状态
Fusion 能力状态（D-035）
```

规则：

```text
1. Context 默认展示引用和摘要，不全量塞入
2. Case 只读参考，不具备执行权
3. Tool/MCP 必须展示风险和权限边界（D-034）
4. Fusion 作为模型能力展示，不作为特殊流程（D-035）
5. 模型输出和资源输出不得显示为 Evidence validated（D-066）
6. 凭据不得展示
```

> 关联决策：D-035, D-034, D-066

---

## 24. 三对象模型弹窗（工作区信息）

工作区信息弹窗对应 D-051 三对象模型（ProjectWorkspace / EnvironmentProfile / ExecutionSession），从顶部状态栏"工作区信息"按钮打开。

### 24.1 三对象

```text
Project Workspace     — 项目文件与产物持久空间（不负责运行环境隔离）
Environment Profile   — Python/Node/JDK/数据库/运行模式声明，可缓存复用（cache_key）
Execution Session     — 一次执行（provider / 风险 / 状态），非项目状态源
```

### 24.2 弹窗规则

```text
1. 展示"文件与环境分离、环境资产可复用、执行会话是一次运行"原则
2. 第一阶段只实现三对象模型展示，不做每项目常驻容器/完整资源调度/自动环境编排/多租户环境池
3. 弹窗信息来自后端，不由前端推断（D-053）
4. 弹窗状态属于 ui_state，不改变业务状态
```

> 关联决策：D-051, D-053

---

## 25. 首次进入 Workspace 引导向导

首次进入新项目的 Workspace 时，弹出引导向导模态框（参考 V10 `ProjectInitWizard` 6 步模式，适配 V26.1 为 5 步）。

### 25.1 引导步骤

```text
1. 环境选择    — 本地环境 / 远程环境（对应 Environment Profile，D-051）
2. 提交方式    — 本地 Git / GitHub 集成（对应 Git 接入，见 08-集成页设计规范.md）
3. 模型配置    — 自动（跟随系统）/ 全局统一 / 自定义（对应 ModelGateway，D-039）
4. 执行模式    — Auto / Plan-Confirm / Manual（D-025）
5. 摘要确认    — 展示 4 项选择 + 确认进入 Workspace
```

### 25.2 引导规则

```text
1. 引导仅在首次进入 Workspace 时显示（guide_completed 标记存储于后端）
2. 源文件检测/克隆不再作为引导步骤（由 P0 接入阶段负责，D-009）
3. 用户可跳过引导（使用默认配置），后续可在项目设置中修改
4. 引导配置写入后端，不由前端本地保存（D-053）
5. 引导 UI 为全屏模态框（z-index 高于 Workspace），步骤指示器展示当前进度
6. 每个步骤提供 2-3 个选项卡片（OptionCard），用户点击选择后进入下一步
7. V10 参考：步骤指示器为圆形编号 + 连接线，完成步骤显示绿色勾
```

> 关联决策：D-009, D-025, D-039, D-051, D-053
> 历史参考：V10 `ProjectInitWizard` 6 步 SOP 初始化模态框（`components/workspace/ProjectInitWizard.tsx`）

---

## 26. Workspace 与 SSE / 事件

Workspace 应订阅与当前 Project/Run/Stage/Node 相关的事件。

建议事件（17 种）：

```text
run_status_changed
stage_status_changed
task_graph_status_changed
node_status_changed
gate_created
gate_decided
evidence_gap_created
evidence_gap_resolved
artifact_created
trace_written
audit_written
workspace_status_changed
file_changed
execution_session_output
command_waiting_gate
external_write_waiting_gate
resync_required
```

规则：

```text
1. 事件不是唯一状态源
2. 事件只作为刷新提示
3. 断线后必须从后端状态恢复
4. resync_required 必须触发重新查询
5. 事件 payload 不得包含密钥
6. mock SSE 必须显式标记（D-049）
```

> 关联决策：D-049

---

## 25. Workspace 状态恢复

Workspace 状态恢复用于处理刷新页面、断线、重新进入、后台任务继续运行等场景（D-053）。

恢复规则：

```text
1. 重新进入 Workspace 时必须查询后端 Project/Run/Stage/Workspace/Gate 状态
2. 不能从终端输出推断任务状态（D-053）
3. 不能从浏览器缓存推断任务状态（D-053）
4. 不能从容器是否运行推断业务状态（D-053）
5. 状态恢复应结合数据库、LangGraph checkpoint、workspace 文件、trace/audit、event log（D-053）
6. full_resync 或 resync_required 必须触发全量刷新相关对象
```

> 关联决策：D-053

---

## 26. Workspace 退出与后台任务

退出 Workspace 时，前端应展示任务状态提示（D-052）。

建议提示：

```text
当前是否有运行中 Run
是否有 Active Gate
是否有执行会话
退出后任务是否继续
如何从项目概览返回
如何打开后台任务面板
```

规则：

```text
1. 退出 Workspace 不等于停止任务（D-052）
2. 低/中风险且无需人工介入任务可后台继续（D-052）
3. 遇 Gate、写盘、shell、外部系统写操作或高风险动作必须暂停（D-034, D-052）
4. 关闭浏览器不等于 cancel
5. cancel/pause/resume 均需走后端 API 和风险策略
```

> 关联决策：D-034, D-052

---

## 27. 响应式与尺寸策略

R1 先按桌面端优先设计。

尺寸策略：

```text
桌面端：    完整六区布局
中等宽度：  右侧检视可折叠
窄屏：      左侧文件面板和右侧检视转为抽屉
移动端：    第一阶段仅保证可查看关键状态和 Gate，不作为主要执行环境
```

规则：

```text
1. 折叠不得隐藏 Active Gate（D-023）
2. 折叠不得隐藏 blocking Evidence Gap（D-066）
3. 窄屏不得把 Gate 降级为普通 toast（D-023）
4. 移动端不承诺完整 IDE 能力
5. R3/R17 根据真实使用情况校准响应式要求
```

> 关联决策：D-023, D-066

---

## 28. 前端状态管理

Workspace 状态应分层（与 `00-UX与前端总览.md` §19 一致）：

```text
server_state        — Project、Run、Stage、Workspace、TaskGraph、Node、Gate、Evidence、Trace、Audit
realtime_hint_state — SSE/Event 的刷新提示
ui_state            — 面板宽度、展开收起、当前 Tab、当前选中文件
draft_state         — 未保存编辑内容
cache_state         — 可丢弃文件预览缓存
mock_state          — 显式标记的 mock 数据（D-049）
```

规则：

```text
1. server_state 必须可重新查询
2. realtime_hint_state 不得成为业务事实
3. ui_state 不得判断 completed（D-053）
4. draft_state 不得伪装为已保存
5. cache_state 不得伪装为最新后端状态
6. mock_state 必须显式标记（D-049）
```

> 关联决策：D-049, D-053

---

## 29. 安全与脱敏

Workspace 是最容易暴露敏感信息的区域，必须重点脱敏（AGENTS.md §12）。

可展示：

```text
credential_status
credential_ref
redacted
has_credential
source_type
source_display_name
trace_ref
audit_ref
file_path 摘要
output_ref
```

不得展示：

```text
Key
Token
Secret
Password
.env 明文
未脱敏连接串
可还原密钥片段
包含凭据的 Git URL
含敏感内容的终端输出片段
含密钥的 Trace/Audit 详情
```

规则：

```text
1. 凭据输入后不回显
2. 密钥文件只显示存在和脱敏状态
3. 执行输出默认脱敏
4. 错误详情不包含凭据
5. Trace/Audit 详情按权限展示
6. 截图、导出、报告不包含密钥
```

---

## 30. Mock 与体验壳规则

R3 可先建设 Workspace 体验壳，但必须显式标记 mock（D-049）。

允许：

```text
静态 Workspace 布局
静态文件树
静态 Agent 对话
静态 TaskGraph
静态终端输出
静态 Evidence/Trace/Audit
静态 Gate 示例
```

禁止：

```text
1. mock Workspace 显示为真实可用
2. mock 文件树显示为真实项目文件
3. mock Run 显示为真实运行
4. mock Gate 显示为真实授权
5. mock Evidence 显示为 validated
6. mock terminal 输出显示为真实执行
7. mock SSE 显示为真实事件
```

> 关联决策：D-049

---

## 31. R2 / R3 / R4 / R8 / R9-R12 校准项

以下校准项供后续 R 阶段执行时对照使用。

### 30.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录
2. Workspace 是否仍为独立全屏 IDE 心智（D-046）
3. 中央类型单例 Tab 是否仍适用（D-047）
4. 文件面板双视图是否与材料身份规范一致（D-048）
5. Gate/Evidence/Trace/Audit 可见性是否足够
6. mock 与脱敏规则是否足够明确
7. 是否重复上级事实，需要改为引用（D-068）
```

### 30.2 R3 前端主干校准

```text
1. 六区布局是否可用
2. 顶部状态栏是否可用
3. 左侧文件面板是否支持代码视图/材料视图
4. 中央类型单例 Tab 是否可用
5. Agent 对话 Tab 是否常驻
6. 底部 Dock 是否可用
7. 右侧 Gate/Evidence/Trace/Audit 是否可见
8. mock 是否显式标记（D-049）
```

### 30.3 R4 API 联调校准

```text
1. Project/Run/Stage API 是否接入
2. Workspace API 是否接入
3. TaskGraph API 是否接入
4. AET API 是否接入
5. Gate/Authorization API 是否接入
6. SSE/Event 是否接入
7. 错误响应是否能正确映射 UI
```

### 30.4 R8 工作区真实化校准

```text
1. 真实文件树是否接入
2. 真实文件预览/编辑是否接入
3. Workspace 文件身份是否接入
4. Execution Session 输出是否接入
5. Workspace 状态恢复是否可用
6. 密钥脱敏是否落实
```

### 30.5 R9-R12 主链路校准

```text
R9： P0-P1 接入、建档、Workspace 文件与材料是否可用
R10：P2-P3 评估、规划、TaskGraph 是否可用
R11：P4 执行、终端、输出、Patch、Tool/MCP 是否可用
R12：P5-P6 验证、Evidence、交付证据链是否可用
```

---

## 32. Project Workspace 红线

```text
1.  不得把 Workspace 做成普通主导航页常驻（D-046）
2.  不得把 Workspace UI 当作状态源（D-053）
3.  不得把终端输出当作状态源（D-053）
4.  不得从浏览器缓存推断业务状态（D-053）
5.  不得把退出 Workspace 解释为任务停止（D-052）
6.  不得隐藏 Active Gate（D-023）
7.  不得把 Gate 显示为普通 toast 后继续执行（D-023）
8.  不得隐藏 Evidence 缺口（D-066）
9.  不得把 Evidence candidate 显示为 Evidence validated（D-066）
10. 不得隐藏 Trace/Audit 缺失
11. 不得把 Artifact 自动显示为项目文档或 Evidence
12. 不得把模型输出或资源输出显示为 Evidence validated（D-066）
13. 不得将 mock Workspace/mock terminal/mock SSE 显示为真实能力（D-049）
14. 不得展示 Key/Token/Secret/Password（AGENTS.md §12）
15. 不得引入 Mission 产品层（D-070）
16. 不得使用旧 Phase/旧 F0-F6/旧 S0-S7 作为当前主流程
17. 不得在长期前端路由、组件、状态字段中固化 V26.1（D-002）
18. 不得把 R1 正式候选契约伪装为实现契约（D-016）
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 33. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1.  明确 Project Workspace 定位（§1）
2.  明确六区总体布局（§2）
3.  明确顶部状态栏与返回平台入口（§3-§4）
4.  明确左侧文件面板、代码视图、材料视图（§5-§7）
5.  明确中央 12 种类型单例 Tab（§8-§14）
6.  明确 Agent 对话、阶段页、TaskGraph、文件、Artifact、Evidence、Diff/Patch Tab（§9-§14）
7.  明确底部 Dock、终端、输出、任务进度（§15-§17）
8.  明确右侧 7 类检视（Gate/Evidence/Trace/Audit/Context/Resource/Model）（§18-§22）
9.  明确 Workspace 与 SSE/Event 的关系（§23）
10. 明确 Workspace 状态恢复（§24）
11. 明确 Workspace 退出与后台任务（§25）
12. 明确响应式与尺寸策略（§26）
13. 明确前端状态管理 6 层（§27）
14. 明确安全与脱敏（§28）
15. 明确 Mock 与体验壳规则（§29）
16. 明确 R2/R3/R4/R8/R9-R12 校准项（§30）
17. 明确 Project Workspace 红线 18 条（§31）
18. 未新增产品决策
19. 未引入 Mission 产品层
20. 未固定最终实现
```
