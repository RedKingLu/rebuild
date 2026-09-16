# 03-Agent与Skill规范

> 文档路径：`文档/04-模型与资源/03-Agent与Skill规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.2
> 来源草稿：`产物/已完成/R1/03-Agent与Skill规范.md`（v0.1，~776 行；去重 ~55%，主要移除与 AGENTS.md/术语表/架构总纲重复的定位/切换条件复述 + 字段枚举）
> 最后更新时间：2026-06-24
> 修订说明：R2：新增引用 D-073 平台助手（§1.6 边界：平台助手为平台级使用助手，不属本文 Agent/Skill 体系、不登记为资源 Agent）；R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`04-模型与资源/` 专题的第 3 份子文档。定义 Agent 类型的具体职责/禁止职责、Agent Definition Contract 契约模板、Skill 分类体系、R 系列 vs P 系列 Skill 隔离规则。Agent/Skill 的定位定义和切换条件见 `AGENTS.md` §14（执行准则详述源）和 `00-项目治理/01-决策记录.md` D-016/D-018（治理详述源）。
> 上级依据：`文档/04-模型与资源/00-模型与资源总览.md`、`文档/00-项目治理/01-决策记录.md` D-016/D-018/D-008-REV1、`AGENTS.md` §14、`文档/02-架构设计/00-架构总纲.md` §2.4+§6。
> 重要边界：本文是 Agent 与 Skill 的**操作规范**（类型定义、契约模板、分类隔离），不重复上游已定义的定位/原则/切换条件，不固化最终 API/DB schema（归 R4/R6）。

---

## 0. 编写原则与上游分工

本文遵守事实源层级（D-068）。Agent/Skill 相关概念的详述源约定：

```text
Agent/Skill 基本定义     → 02-术语表.md（第四部分）
少 Agent 多 Skill 原则    → 01-决策记录.md D-016
Agent 切换条件（7 项）     → AGENTS.md §14.1
Skill 分类（R/P 系列）    → AGENTS.md §14.2
Agent Definition Contract → 01-决策记录.md D-018（13 要素）+ 本文 §3（模板展开）
P 系列 Skill 目录归属     → 01-决策记录.md D-008-REV1
Agent 类型清单             → 00-架构总纲.md §2.4+§6
```

本文不重复上游已定义的内容。本文展开：每种 Agent 类型的具体职责/禁止（上游只列名字）、Agent Definition Contract 模板、Skill 分类体系、R/P 隔离操作规则。

---

## 1. Agent 类型定义

> 类型清单来自 `00-架构总纲.md` §2.4。本文展开每种类型的具体职责/禁止职责（上游未展开）。

第一阶段的 5 种必要 Agent 类型：

### 1.1 Node Worker Agent

**职责**：执行 Task Node → 调用 Skill → 调用 ModelGateway → 调用受控 Tool/MCP → 生成节点输出 → 生成 Artifact/Evidence 候选 → 执行自验证 → 提交 Acceptance

**禁止**：自行批准高风险动作 / 自行关闭 Gate / 自行标记阶段 completed / 把模型输出标记为 Evidence validated

### 1.2 Acceptance Agent

**职责**：检查 Node Worker 输出 → 检查与 Task Plan/Stage Plan 对齐 → 检查 Artifact/Evidence → 检查 Trace/Audit → 给出结论（accepted / rework_required / retry_required / gate_required / failed）

**禁止**：替代 P5 验证 / 替代用户 Gate / 批准 Policy 禁止项 / 把缺 Evidence 的结果标记为可信完成

### 1.3 Auto Review / Authorization / Safety Agent

**职责**：在 Auto Mode 内辅助审核 Stage Plan/Task Plan → 辅助判断风险级别 → 辅助判断是否越界 → 辅助判断是否需要 Gate → 辅助处理安全与授权建议

**禁止**：批准 Policy 禁止项 / 批准 L5 高风险动作 / 绕过用户阶段晋级 Gate / 绕过 Audit

### 1.4 Expert Agent

**职责**：提供特定技术栈或领域专家能力 → 辅助复杂迁移判断 → 辅助安全/性能/兼容性/验证分析 → 辅助确定性转换资源选择 → 辅助解释复杂风险

**禁止**：直接成为最终验收主体 / 直接批准高风险执行 / 直接跳过 P5 Evidence / 直接关闭用户 Gate

### 1.5 Conversation / Gate Agent

**职责**：与用户交互 → 解释 Gate（原因/风险/选项/影响）→ 收集用户决策 → 将用户决策写回 Gate 流程

**禁止**：伪造用户授权 / 默认批准高风险动作 / 隐藏 Evidence 不足 / 隐藏风险或冲突

### 1.6 边界：平台助手不属于本文 Agent / Skill 体系（引用 D-073）

平台助手（AI 精灵 / 客服对话 Agent）是**平台级使用助手**（浮动图标 + 对话面板，对话答疑 + 使用引导），**不属于**本文规范的 P 系列资源 Agent / Skill 体系，也不是项目内运行时 Agent；不进 LangGraph 主编排，**不得**登记为一类资源 Agent。其规范（含代操作授权、模型连通性自测、Audit 边界）见 `01-决策记录.md` D-073。

> 本文只记此边界，不复制 D-073 全文。

---

## 2. Agent 切换条件与规则

> 切换条件 7 项见 `AGENTS.md` §14.1。本文只展开**切换时的操作规则**（上游未覆盖）。

```text
1. 切换原因必须记录
2. 切换前后上下文传递必须明确（Context Recipe 交接）
3. 切换前后 Artifact/Evidence 传递必须可追踪
4. 切换不得丢失 Trace
5. 权限提升必须 Gate 或 Policy 允许
6. 切换不得绕过 Acceptance
7. 切换不得绕过用户 Gate
```

**不得**随意新增 Agent——以下情况优先用 Skill/Template/Context Recipe/Tool Policy 解决：

```text
只是换一个提示词 / 只是换一个阶段名 / 只是换一种输出模板
/ 只是换一个检查清单 / 只是为了显得架构完整 / 只是为了复刻旧 Agent 体系
```

---

## 3. Agent Definition Contract 模板

> D-018 定义了 13 个必备要素。本文展开为可操作的契约模板。

每个 Agent 必须通过结构化契约定义。必备要素（D-018）：

```text
1. 职责（responsibilities）           8. Tool Policy（可调用哪些 Tool）
2. 禁止职责（forbidden）              9. Skill Policy（可调用哪些 Skill）
3. Context Recipe（上下文组装规则）    10. MCP Policy（可访问哪些 MCP）
4. Memory Policy（记忆读写规则）      11. 输入输出契约（input/output contract）
5. Model Policy（默认模型策略）        12. Artifact/Evidence 契约
6. Gate 触发规则                      13. 自验证清单 + 验收标准 + 失败升级规则
7. 失败升级规则
```

> R4/R6 校准：Agent Definition 的具体字段（agent_id, agent_type, version, status 等 ~20 项）由 R4 数据建模和 R6 实现时定义。本文不固化最终 schema。Agent Definition 不得保存明文密钥，不得把 Policy 禁止项写成可批准项。

---

## 4. Skill 分类体系

### 4.1 R 系列 vs P 系列（根本区分）

| 维度 | R 系列建设 Skill | P 系列平台运行 Skill |
|---|---|---|
| **用途** | rebuild 当前版本建设（文档/施工/审核/验收/交接） | 未来用户项目 P0-P6 流程 |
| **归属** | `skills/R-建设执行/` | `平台资源/skills/P系列/` |
| **调用主体** | R 阶段 Agent | Node Worker Agent |
| **生命周期** | 跟随 R 阶段（建设完成后可归档） | 跟随平台版本（长期维护） |
| **验收方式** | R 阶段验收 | 平台运行期验收 |

隔离规则（D-008-REV1）：

```text
1. R 系列与 P 系列不得混放同一目录
2. R 系列不得被误当作平台运行期用户能力
3. P 系列不得被误用于平台建设流程
4. 两者事实源、生命周期、权限、调用主体、验收方式必须区分
5. 如需复用方法，必须明确复制/引用/改写关系和适用范围
```

### 4.2 Skill 按用途分类（R1 建议，R6 校准）

```text
stage_skill       — 阶段执行 Skill（如 P0 接入、P1 建档）
planning_skill    — 规划 Skill（Stage Plan/Task Plan 生成）
execution_skill   — 执行 Skill（代码修改、Patch 生成）
validation_skill  — 验证 Skill（测试、比对、回归检查）
review_skill      — 评审 Skill（方案评审、风险评估）
resource_skill    — 资源调用 Skill（Tool/MCP/Expert Agent 编排）
documentation_skill — 文档 Skill（报告、交付说明生成）
handoff_skill     — 交接 Skill（阶段交接材料）
construction_skill — 平台建设 Skill（R 系列专用）
```

> 上述分类为 R1 建议，R6 实现时可能合并/拆分/重命名。分类维度当前混合了"按阶段"和"按活动"——R2 需确认是否接受或调整为更一致的分法。

---

## 5. P 系列 Skill 规划（R9-R12 施工）

P 系列 Skill 按 P0-P6 阶段组织（D-008-REV1）：

```text
平台资源/skills/P系列/
├── P0-接入Skill.md
├── P1-建档Skill.md
├── P2-评估Skill.md
├── P3-规划Skill.md
├── P4-执行Skill.md
├── P5-验证Skill.md
└── P6-交付Skill.md
```

> R9-R12 链路施工时逐份创建。文件名和具体划分以施工时决策为准。R1 仅作规划占位。

---

## 6. 与其他组件的关系（速查）

| 组件 | Agent 侧约束 | Skill 侧约束 |
|---|---|---|
| **ModelGateway** | 必须通过 ModelGateway 调用模型；不得直接调 Provider | 可声明 required_model_policy；不得包含 Provider 密钥 |
| **Tool/MCP/Expert Agent** | 按 Tool/Skill/MCP Policy 调用；外部写操作必须 Gate | 可声明 required_tools；Skill 中的步骤不得隐藏失败路径 |
| **NodeLoop** | 输出必须回到 NodeLoop；自验证不能替代 Acceptance | 不得绕过 NodeLoop 隐式执行；步骤不得隐藏失败 |
| **Gate/Policy/Audit** | 不能批准 Policy 禁止项；L5 必须用户 Gate | 不能覆盖 Policy；触发 Gate 时须说明原因/风险/选项 |
| **Artifact/Evidence/Trace** | Artifact 必须关联 Project/Run/Stage/Node；模型输出≠Evidence | Evidence 必须支撑明确 claim；不足不得 completed |
| **Case/Knowledge** | Case 不能直接执行 | 社区 Skill 默认只读参考，执行前须经审核 |

---

## 7. Skill 安全与密钥

```text
1. Agent/Skill 不得保存明文密钥
2. Agent/Skill 不得将密钥写入 prompt
3. 可声明密钥需求，不记录密钥值
4. 可识别密钥文件是否存在，不展示内容
5. 发现密钥泄露风险必须暂停 → Gate 或安全处理流程
```

> 密钥脱敏（D-032）等安全红线就地保留；通用禁止与红线完整总表另见 AGENTS.md §18。

---

## 8. R2/R4/R6/R9-R12 校准项

### R2 文档校准

```text
1. 是否对齐最新决策记录（D-016/D-018/D-008-REV1）
2. Agent 类型是否过多或不足（当前 5 种）
3. Skill 分类 9 种是否接受（当前混合了"按阶段"和"按活动"两个维度）
4. R 系列/P 系列 Skill 路径是否与实际目录一致
5. 是否仍有 Mission/mission_id 残留
```

### R4 工程骨架

```text
Agent/Skill 数据模型；Registry 结构；API 端点；Trace/Audit Writer 接入
```

### R6 Agent/Skill/资源基础

```text
Agent Definition Contract 实现；Skill 目录落地；ModelGateway/Tool/MCP 对接；权限策略；Registry
```

### R9-R12 主链路

```text
R9（P0-P1）：最小 Agent/Skill 就绪
R10（P2-P3）：评估与规划 Skill 就绪
R11（P4）：执行 Skill + Node Worker Agent 就绪
R12（P5-P6）：验证/交付 + Acceptance Agent 就绪
```

---

## 9. Agent / Skill 红线

```text
1. 不得为相似职责过度拆分 Agent
2. 不得让 Skill 脱离 Agent 独立执行
3. 不得让 Skill 绕过 Agent 权限
4. 不得混放 R 系列建设 Skill 与 P 系列平台运行 Skill
5. 不得让 Agent/Skill 绕过 ModelGateway 直接调用 Provider
6. 不得让 Agent/Skill 绕过统一资源调用流程
7. 不得让 Agent/Skill 绕过 Policy/Gate/Audit
8. 不得用 Agent 自评替代 Acceptance
9. 不得用 Acceptance 替代 P5 验证
10. 不得用 Agent/Skill 输出替代 Evidence
11. 不得将 Case 直接转为可执行 Skill
12. 不得默认执行社区 Skill
13. 不得泄露 Key/Token/Secret/Password
14. 不得引入 Mission 产品层
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 10. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 5 种 Agent 类型的具体职责/禁止职责（§1）——上游只列名字，本文展开
2. 明确 Agent 切换时的 7 条操作规则 + 6 条禁止随意新增（§2）
3. 明确 Agent Definition Contract 13 要素模板（§3）
4. 明确 Skill R/P 二分 + 9 种用途分类 + 5 条隔离规则（§4）
5. 明确 P 系列 Skill 按 P0-P6 规划（§5）
6. 明确 Agent/Skill 与其他组件的关系速查（§6）
7. 明确 Skill 安全与密钥规则（§7）
8. 明确 R2/R4/R6/R9-R12 校准项（§8）
9. 明确红线 14 条（§9）
10. 未重复 AGENTS.md §14 的定位/切换条件/原则复述
11. 未固化 Agent/Skill Definition 字段（→R4/R6）
12. 未引入 Mission 产品层
13. 未新增产品决策
```
