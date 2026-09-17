# 04-平台资源与Registry规范

> 文档路径：`文档/04-模型与资源/04-平台资源与Registry规范.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/04-平台资源与Registry规范.md`（v0.1，~965 行；去重 ~61%，主要移除与 00 总览/术语表/决策记录重复的资源类型定义 + 字段枚举 + 通用规范复述）
> 最后更新时间：2026-06-24
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：`04-模型与资源/` 专题的第 4 份子文档。定义平台资源的 Registry 登记规范、来源与信任等级分类、资源调用完整流程、Case 转化规则、版本与替代管理。资源类型定义和红线见 `00-模型与资源总览.md` §3-§4（专题入口详述源）和 `02-术语表.md` 第十部分（治理详述源）。
> 上级依据：`文档/04-模型与资源/00-模型与资源总览.md`、`文档/00-项目治理/01-决策记录.md` D-041/D-042/D-008-REV1/D-063。
> 重要边界：本文不重复 00 总览的资源类型定义和红线、不重复术语表的各资源类型详述、不固化 Registry/调用记录的字段 schema（归 R4/R6）。

---

## 0. 编写原则与上游分工

本文遵守事实源层级（D-068）。详述源约定：

```text
资源类型定义（9 类）      → 02-术语表.md 第十部分 + 00-模型与资源总览.md §3
资源风险分级 L0-L5        → 02-术语表.md 第七部分 + 01-决策记录.md D-042
资源调用统一流程原则      → 01-决策记录.md D-041 + 00-模型与资源总览.md §5
确定性转换接入方式        → 01-决策记录.md D-063
P 系列 Skill 目录归属     → 01-决策记录.md D-008-REV1
平台资源红线              → 00-模型与资源总览.md §4
```

本文展开 Registry 层操作规范：来源信任模型、完整调用流程（14 步）、Case 转化前提、版本管理、MCP/Expert Agent 额外约束——这些是上游有意留白的"登记与管理"层。

---

## 1. Registry 定位

Registry 是平台资源的**登记与索引层**——不执行资源，只管理资源的元信息。

```text
Registry 负责 10 件事：
1. 登记资源身份（resource_id/type/name）
2. 登记资源来源（source_type/source_trust_level）
3. 登记版本和状态
4. 登记风险级别
5. 登记权限边界
6. 登记输入输出契约
7. 登记 Artifact/Evidence 契约
8. 登记 Trace/Audit/Gate 策略
9. 管理版本替代链（supersedes/superseded_by）
10. 支撑资源查询、选择、调用前检查和版本解析

Registry 不负责 7 件事：
直接执行资源 / 批准资源调用 / 覆盖 Policy / 保存明文密钥
/ 替代具体资源定义文件 / 替代 Artifact/Evidence 存储 / 替代运行时 Trace/Audit
```

> R4/R6 校准：Registry 具体字段（resource_id, resource_type, version, status, source_type, risk_level, permission_scope, input/output/artifact/evidence contract, trace/audit/gate policy, supersedes/superseded_by 等 ~30 项）由 R4 数据建模定义。本文只规定 Registry 的职责边界和登记规则。Registry 不得保存明文密钥。

---

## 2. 来源与信任等级

### 2.1 来源分类（source_type）

```text
internal_current   — 当前版本内部资源（已审核）
internal_archive   — 历史归档资源（只读参考）
user_provided      — 用户提供资源
community          — 社区资源（默认只读）
external_online    — 在线资源（默认只读）
third_party        — 第三方资源（需审核许可）
generated          — 平台生成资源
```

### 2.2 信任等级（source_trust_level）

```text
trusted_current      — 当前版本已审核，可调用
reviewed_reference   — 已审核参考，受控引用
read_only_reference  — 只读参考，不得执行
unreviewed           — 未审核，不得执行
blocked              — 禁用或阻塞
unknown              — 未知来源，不得进入 active
```

### 2.3 规则

```text
1. 历史版本只读参考，不自动成为当前事实源
2. 社区资源默认 read_only_reference（D-041）
3. 未审核资源不得执行——状态不得为 active
4. 来源不明（unknown）资源不得进入 active
5. 外部资源执行前必须检查许可、权限、安全和风险
6. 信任等级变更必须记录原因和审核记录
```

---

## 3. 资源调用完整流程

> 原则见 D-041 + 00 总览 §5。本文展开为可操作的完整流程。

### 3.1 调用流程（14 步）

```text
1. NodeLoop/Agent 识别资源需求
2. 查询 Registry（验证资源存在且状态允许调用）
3. 读取资源定义（input/output/artifact/evidence contract）
4. 检查资源状态（active/read_only/deprecated/disabled）
5. 检查来源和许可（source_type/source_trust_level/license）
6. 检查风险级别（L0-L5）
7. 检查权限边界（permission_scope）
8. 检查 Policy/Hook（Policy 禁止项 → 立即停止，不进入后续步骤）
9. 判断是否需要 Gate（L3-L5/外部写/未审核→Gate）
10. 执行只读引用或受控调用
11. 记录 Trace（必须——所有资源调用）
12. 必要时记录 Audit（L3+ / Policy 冲突 / Gate 触发）
13. 输出 Artifact/Evidence 候选或参考结果
14. 结果回到 NodeLoop/TaskGraph 路由
```

### 3.2 调用前必须确认（11 项）

```text
resource_id / resource_status / source_type / source_trust_level
/ risk_level / permission_scope / input_contract / output_contract
/ gate_policy / trace_policy / audit_policy
```

### 3.3 调用后必须记录

```text
所有调用 → Trace（resource_call_id / resource_id / called_by / input_refs / output_refs / status）
高风险调用 → +Audit（gate_ref / audit_ref / policy_check_result_ref）
失败调用 → +error_ref / failure_policy 路由
```

> R4 校准：调用记录具体字段由 R4 定义。

---

## 4. 资源状态与版本管理

### 4.1 状态流转规则

资源状态由 R4 定义（本文不固化枚举）。核心规则：

```text
1. draft/under_review → 不得被自动调用
2. active → 可在权限和 Policy 允许下调用
3. read_only → 可引用，不得执行
4. deprecated → 不得作为默认选择；调用时警告
5. superseded → 必须保留替代链（supersedes/superseded_by）
6. disabled/blocked → 不得调用；blocked 必须说明阻塞 Policy
7. archived → 仅参考
```

### 4.2 版本管理

```text
1. 资源变更必须生成新版本或记录变更记录——不得静默覆盖 active 资源
2. supersedes/superseded_by 必须显式维护——形成可追溯替代链
3. 高风险资源（L3+）变更必须审核
4. Policy 变更必须审核（Policy 是硬约束）
5. 社区资源升级为可执行资源必须审核
6. 审查记录至少包含：review_id / reviewer / review_type / review_result / risk_notes / security_notes
```

---

## 5. Case 与社区资源的转化规则

### 5.1 Case → Tool/Skill/Template 转化前提

> Case 无执行权（术语表 §10）。转化必须满足以下 6 条：

```text
1. 明确转化原因——为什么引用不足以满足需求
2. 登记来源——原 Case 的 source_ref 必须保留
3. 进行适配——不得直接复制，必须适配当前版本上下文
4. 经过安全、许可、权限和审核流程
5. 生成新的资源定义——新 resource_id，新版本
6. 不得覆盖原 Case 身份——原 Case 仍作为只读材料保留
```

### 5.2 社区资源升级为可执行

```text
1. 默认登记为 L0 / read_only_reference
2. 执行前必须经过安全、许可、权限和审核流程（D-041）
3. 升级为 active Tool/Skill/MCP 时必须新建资源定义（新 resource_id）
4. 必须记录来源和适配方式
5. 不得直接复制未经确认的外部代码进入当前实现
6. 不得泄露密钥到外部资源
```

---

## 6. MCP / Expert Agent 额外约束

> 通用规则（必须登记 Registry、必须有 Trace、高风险必须 Gate/Audit）见 00 总览 §3。本文只列这两类资源的**额外**约束。

### 6.1 MCP 额外要求

```text
1. 必须记录连接来源和可访问范围
2. 必须记录是否可写（write_enabled）
3. 必须记录凭据需求引用（不记录凭据值）
4. 外部系统写操作必须 Gate
```

### 6.2 Expert Agent 额外要求

```text
1. 必须说明专家领域和适用场景
2. 不得成为最终裁决主体
3. 不得替代 P5 Evidence
4. 不得替代用户 Gate
```

---

## 7. 与其他组件的关系（速查）

| 维度 | 关键约束 |
|---|---|
| **P0-P6** | 资源调用不得改变阶段契约；不得绕过阶段晋级 Gate；P5 验证必须基于可验证 Evidence |
| **TaskGraph/NodeLoop** | 调用发生在 NodeLoop 内；TaskGraph Edge 声明风险策略和失败策略；失败进入 failure_policy；结果回到 TaskGraph 路由 |
| **Gate** | 触发场景：高风险资源/L3+外部写/未审核执行请求/社区资源升级/Policy 冲突/Evidence 不足 |
| **Trace** | 所有资源调用必须 Trace；记录：Registry 查询/调用/输入输出/失败重试 fallback/版本和来源 |
| **Audit** | 触发场景：高风险调用/L3+外部写/Policy 冲突/社区升级/删除或不可逆/验收交付相关 |
| **安全** | Registry 不得保存明文密钥；资源定义不得保存明文密钥；凭据只记录引用不记录值 |

---

## 8. R2/R4/R6/R14/R15 校准项

### R2 文档校准

```text
1. 平台资源目录是否与实际磁盘状态一致
2. Registry 字段粒度是否合适
3. Case 是否仍严格限定为不可执行
4. 社区资源是否仍默认只读
5. 是否仍有 Mission/mission_id 残留
```

### R4 工程骨架

```text
Registry 数据结构；资源状态模型；调用 API；Trace/Audit Writer；Policy/Hook 接入；Gate 集成
```

### R6 Agent/Skill/资源基础

```text
平台资源目录落地；Agent/Skill/Tool/MCP/Case/Knowledge/Policy/Template 登记；Registry 初版；调用流程；风险分级；R/P Skill 隔离
```

### R14 远程资源

```text
远程资源来源登记；权限边界；外部写 Gate；远程 Trace/Audit；安全脱敏；失败回滚路径
```

### R15 社区功能

```text
社区资源默认只读落实；Case/Skill/Template/Tool 审核流程；许可与安全记录；升级 Gate/Audit；前端标注
```

---

## 9. 红线

```text
1. 不得将 Case 直接执行
2. 不得将社区资源默认执行
3. 不得将未审核资源标记为 active
4. 不得将 read_only 资源用于执行
5. 不得让资源调用绕过 Registry
6. 不得让资源调用绕过 Policy/Gate/Audit
7. 不得让资源调用绕过 NodeLoop/TaskGraph
8. 不得无 Trace 调用资源
9. 不得无 Audit 执行高风险资源调用（L3+）
10. 不得静默覆盖资源版本
11. 不得把确定性转换设计为独立主线引擎（D-063）
12. 不得把 Template 当作事实源
13. 不得泄露 Key/Token/Secret/Password
14. 不得引入 Mission 产品层
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 10. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确 Registry 定位——10 项负责 + 7 项不负责（§1）
2. 明确来源分类（7 种）+ 信任等级（6 种）+ 6 条规则（§2）
3. 明确资源调用完整流程——14 步 + 11 项调用前确认 + 调用后记录要求（§3）
4. 明确资源状态流转规则 + 版本管理 6 条（§4）
5. 明确 Case→Tool/Skill 转化 6 条前提 + 社区资源升级规则（§5）
6. 明确 MCP 4 条额外要求 + Expert Agent 4 条额外要求（§6）
7. 明确与其他组件的关系速查——6 维度（§7）
8. 明确 R2/R4/R6/R14/R15 校准项（§8）
9. 明确红线 14 条（§9）
10. 未重复 00 总览的资源类型定义、风险分级详述、通用规范
11. 未固化 Registry/调用记录字段 schema（→R4）
12. 未引入 Mission 产品层
13. 未新增产品决策
```
