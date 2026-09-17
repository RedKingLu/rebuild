# 06-Model-Resource API建议契约

> 文档路径：`文档/05-API与集成契约/06-Model-Resource API建议契约.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.3
> 来源草稿：`产物/已完成/R1/06-Model-Resource%20API建议契约.md`（v0.1）
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 文档定位：定义 rebuild 当前版本中 ModelGateway、模型配置、模型策略、模型调用、Fusion 模型能力、Resource Registry、Skill/Tool/MCP/Expert Agent/Case/Knowledge/Template/Policy 等资源相关 API 的 R1 建议契约。
> 上级依据：`文档/00-项目治理/01-决策记录.md`（D-016, D-039, D-065）、`文档/04-模型与资源/00-模型与资源总览.md`、`文档/04-模型与资源/04-平台资源与Registry规范.md`、`文档/04-模型与资源/05-资源调用与Case-Knowledge-Tool-MCP边界.md`、`文档/04-模型与资源/06-确定性转换资源接入规范.md`、`文档/05-API与集成契约/00-API与集成契约总览.md`（§13-§14）。
> 重要边界：本文是 Model / Resource API 的 R1 建议契约，所有端点、字段、状态和错误码均为 R1 建议契约，R2/R4/R5/R6/R10-R12 需按真实实现校准为实现契约。
> 修订说明：R1 正式化版基于草稿 v0.1：(1) 头部标准化；(2) §0 新增关联决策速查表；(3) 关键节加注决策引用；(4) 新增 §24 待确认项（4 项）；(5) 验收标准扩展（19→21 项）。R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。

---

## 0. 编写原则

本文遵守事实源层级（D-068）：项目治理 > 产品定义 > 架构设计 > 专题规范。

**关联决策速查**：

| 决策编号 | 内容 | 本文相关节 |
|---|---|---|
| D-016 | API 字段 R1 建议，R2 校准 | 全文 |
| D-039 | ModelGateway + LiteLLM 薄适配 | §3, §8 |
| D-065 | 薄编排/NIH 约束——不重复实现 LiteLLM 已覆盖能力 | §3 |
| D-068 | 单一事实源 | §0 |

**字段规范引用**：本文遵守 `01-字段规范与错误响应规范.md`。上级设计遵守 `04-模型与资源/` 系列文档。

本文必须遵守：

```text
1. API 字段 R1 输出建议契约，R2 校准为实现契约；
2. 模型选择不绑定阶段，按用户临时指定→Task/Node override→Agent 默认→Project 默认→System 默认解析；
3. ModelGateway 采用 rebuild 自研抽象层 + LiteLLM SDK Adapter 暂定路线（D-039/D-065）；
4. Fusion 是模型能力或模型策略，不作为特殊流程/阶段/评审引擎；
5. Fusion 不绕过 Policy/Gate/Audit，输出不自动等于授权或验收通过；
6. token/成本预算为模型模块功能，非主线，不作为阶段 Gate 默认条件；
7. Case 不是 Skill/Tool，不具备执行权；
8. 资源调用必须记录来源、风险、权限和 Trace；
9. 资源使用按风险分级 L0-L5；
10. 社区资源默认只读参考；
11. 确定性转换以 Skill/Tool/Expert Agent/MCP 资源形式接入，不做独立引擎主线；
12. 模型/资源/确定性转换均不得绕过 Policy/Gate/Audit；
13. API/响应/错误/Trace/Audit/事件不得泄露 Key/Token/Secret/Password（D-032）。
```

本文不得：将 Fusion 设计成特殊流程、让 Case 直接执行、将社区资源默认 active、让 Tool/MCP 绕过统一资源调用流程、将模型密钥返回前端（D-032）、将 token 预算设计为阶段 Gate、引入 Mission 产品层（D-070）。

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 对象边界

```text
Model Provider：模型服务商；
Model Profile：模型配置档案；
Model Policy：模型选择与调用策略；
Model Binding：Project/Agent/Task/Node 级模型绑定；
Model Call：一次模型调用；
Fusion Capability：Fusion 模型能力；
Resource：平台资源；
Resource Registry：资源登记与索引；
Resource Call：一次资源调用；
Skill/Tool/MCP/Expert Agent：可执行资源；
Case/Knowledge/Template：只读参考资源；
Deterministic Converter：确定性转换资源。
```

边界规则：

```text
1. ModelGateway 管模型调用，不负责主流程编排；
2. Case 不具备执行权；
3. Tool/MCP 可执行但必须经过资源调用流程；
4. Expert Agent 可提供专业判断，但不得批准 Policy 禁止项；
5. 确定性转换器是资源，不是独立主线引擎。
```

---

## 2. API 路由归口建议

```text
/api/model/providers
/api/model/profiles
/api/model/policies
/api/model/bindings
/api/model/calls
/api/model/calls/{model_call_id}/trace
/api/model/fusion/capabilities
/api/model/usage
/api/resources
/api/resources/{resource_id}
/api/resources/registry
/api/resources/{resource_id}/checks
/api/resources/{resource_id}/calls
/api/resources/calls/{resource_call_id}
/api/resources/calls/{resource_call_id}/trace
/api/resources/calls/{resource_call_id}/audit
/api/resources/converters
/api/resources/converters/{resource_id}
```

---

## 3. ModelGateway 核心契约

### Model Provider

字段：`provider_id`, `provider_name`, `credential_ref`（只传引用，不返明文）, `credential_status`（exists/missing/invalid/redacted）, `supported_capabilities`, `status`, `last_checked_at`

### Model Profile

字段：`model_profile_id`, `provider_id`, `model_name_ref`, `model_family`, `capability_tags`, `default_parameters`（不得含密钥）, `context_limit_note`, `usage_policy_ref`, `cost_policy_ref`, `status`

### Model Policy

模型选择优先级（从上到下）：

```text
1. 用户临时指定
2. Task / Node override
3. Agent 默认模型
4. Project 默认模型
5. System 默认模型
```

字段：`selection_order`, `allowed_profiles`, `blocked_profiles`, `default_profile_ref`, `fallback_profile_refs`, `fusion_allowed`, `usage_budget_policy`, `trace_policy`, `audit_policy`

### Model Binding

字段：`scope_type`（system/project/agent/task/node）, `scope_ref`, `model_profile_ref`, `model_policy_ref`, `priority`

### Model Call

请求：`project_id`, `run_id`, `stage`, `node_id`, `agent_ref`, `skill_ref`, `model_policy_ref`, `model_override`, `input_context_refs`（只传引用）, `call_purpose`, `risk_level`

响应：`model_call_id`, `selected_model_ref`, `provider_ref`, `model_call_status`, `output_ref`, `usage_summary`, `trace_ref`, `error_ref`

规则：模型调用必须经 ModelGateway、输出不自动成为 Evidence、Trace 不得含密钥明文、高风险结果用于决策需 Gate/Audit。

---

## 4. Fusion 能力 API

Fusion 是模型能力或模型策略——不作为特殊流程/阶段/评审引擎。Fusion 输出不自动等于授权或验收通过。高风险建议保留 Trace，按风险进入 Gate/Audit。

字段：`fusion_capability_id`, `model_profile_ref`, `capability_tags`, `recommended_use_cases`, `not_recommended_use_cases`, `risk_notes`

---

## 5. Model Usage / 成本预算 API

token/成本预算归模型模块，不阻塞 P0-P6 主链路，不作为阶段 Gate 默认条件。

字段：`usage_summary`, `cost_summary`, `budget_policy_ref`（R1 建议，R2/R5 校准具体指标）

---

## 6. Resource Registry 核心契约

### Resource 登记

字段：`resource_id`, `resource_type`（skill/tool/mcp/expert_agent/case/knowledge/template/policy/deterministic_converter/model_profile/external_resource）, `name`, `version`, `status`, `source_type`, `source_ref`, `risk_level`, `permission_scope`, `gate_policy`, `trace_policy`, `audit_policy`, `review_status`, `input_contract`, `output_contract`, `supersedes`, `superseded_by`

### Resource 状态

`draft → under_review → approved → active | read_only | deprecated | superseded | disabled | blocked | archived`

规则：未审核不得 active、社区资源默认 read_only、Case 不得登记为 executable、read_only/disabled/blocked 不得调用。

### Resource Call 前检查

响应关键字段：`applicable`, `allowed`, `risk_level`, `requires_gate`, `requires_audit`, `policy_check_ref`, `blocking_reasons`

规则：allowed=true 不代表无需 Gate、requires_gate=true 必须创建 Gate、Policy 禁止不得继续。

### Resource Call

规则：必须先通过调用前检查、Case/Knowledge 只读不可执行、Tool/MCP 执行遵守风险级别、外部系统写必须 Gate/Audit、输出不自动=Evidence validated。

---

## 7. 确定性转换资源 API

以 Skill/Tool/Expert Agent/MCP 形式接入，不构建独立引擎主线。

字段：`converter_type`, `supported_languages`, `supported_frameworks`, `transformation_rules_ref`, `dry_run_supported`, `rollback_supported`, `idempotency_note`, `changed_files`, `patch_ref`, `report_ref`

规则：优先 dry-run 或生成 Patch 草案、写盘受 write_scope 控制、高风险转换必须 Gate/Audit、转换成功≠P5 验证通过。

---

## 8. Case / Knowledge API

Case 不是 Skill/Tool，不具备执行权。Case 禁止直接转为可执行脚本/Tool/Patch。Knowledge 默认可检索、可引用，不自动成为当前事实。历史材料只读参考。

字段：`trust_level`, `applicable_context`, `related_stages`, `related_skills`, `read_only`（强制 true）

---

## 9. Model / Resource 错误响应

关键错误类型：`credential_missing`, `credential_invalid`, `model_unavailable`, `model_call_failed`, `resource_not_active`, `resource_read_only`, `resource_blocked`, `resource_check_failed`, `unsupported_resource_type`, `policy_blocked`, `gate_required`, `audit_missing`, `trace_missing`, `redaction_required`

规则：credential_* 不得返明文、resource_read_only 不得降级为 warning 后继续执行、model_call_failed 不得暴露内部堆栈。

---

## 10. 前端联调边界

前端必须标记：凭据缺失/无效/已脱敏、资源只读/被禁用、社区资源未审核、高风险资源、需要 Gate、Policy 阻断、mock/未接真实服务。

前端不得：展示模型密钥或 Provider Secret、将 Case 显示为可执行 Tool、将社区资源显示为默认可执行、将 Fusion 显示为独立流程、将模型输出显示为 Evidence validated、将 mock 伪装为真实能力。

---

## 11. R2 / R4 / R5 / R6 / R10-R12 校准项

- **R2**：边界清晰、Fusion 仍为模型能力、Case 仍无执行权、资源风险分级与安全文档一致
- **R4**：FastAPI 路由、Pydantic schema、脱敏 middleware
- **R5**：ModelGateway 抽象层、LiteLLM Adapter、Provider/Profile/Policy/Binding 数据结构、Fusion 能力 API
- **R6**：Resource Registry、Skill/Tool/MCP/Expert Agent 接入、Case/Knowledge 只读边界、确定性转换资源
- **R10-R12**：按阶段校准模型选择与资源调用的可用性

---

## 12. Model / Resource API 红线

```text
1. 不得将 Fusion 设计成特殊流程、特殊阶段或独立运行时角色；
2. 不得让 Fusion 输出自动等于授权、验收或 Evidence validated；
3. 不得让模型输出自动进入 Evidence validated；
4. 不得让模型调用绕过 ModelGateway（D-039）；
5. 不得在 API / Trace / Audit / 前端展示模型密钥（D-032）；
6. 不得将 token / 成本预算作为阶段 Gate 默认条件；
7. 不得让 Case 直接执行；
8. 不得将 Case 直接转为 Tool / Executable Skill / Patch；
9. 不得让社区资源默认执行；
10. 不得让 Tool / MCP / Expert Agent 绕过统一资源调用流程；
11. 不得让确定性转换器绕过 Registry / Policy / Gate / Audit；
12. 不得将资源输出自动标记为 Evidence validated；
13. 不得无 Trace 执行资源调用；
14. 不得无 Audit 执行高风险资源调用；
15. 不得引入 Mission 产品层（D-070）；
16. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 13. 本文验收标准

```text
1. 明确 Model / Resource 对象边界；
2. 明确 API 路由归口建议；
3. 明确 ModelGateway API（Provider/Profile/Policy/Binding/Call）；
4. 明确 Fusion 能力 API（模型能力，非特殊流程）；
5. 明确 Model Usage / 成本预算 API（非主线 Gate）；
6. 明确 Resource Registry 登记、类型（11种）、状态（10种）；
7. 明确 Resource Call 前检查与调用契约；
8. 明确确定性转换资源 API（非独立引擎）；
9. 明确 Case / Knowledge API（只读，无执行权）；
10. 明确错误响应；
11. 明确事件建议；
12. 明确前端联调边界；
13. 明确 R2/R4/R5/R6/R10-R12 校准项；
14. 明确 Model / Resource API 红线（16条）；
15. 未新增产品决策；
16. 未引入 Mission 产品层；
17. 未固定最终实现 schema；
18. 决策交叉引用完整（D-016/D-039/D-065/D-068）；
19. 待确认项显式列出。
```

---

## 14. 待确认项

```text
1. §4 Fusion 能力的 API 粒度——当前为 capabilities + evaluations 两层。若 Fusion 确实"只是一个模型能力"，evaluations 端点是否需要独立存在还是合并到 Model Call 的 output 中。

2. §5 token/成本预算字段——usage_summary 和 cost_summary 为 R1 建议占位，R5 需根据 LiteLLM 实际可获取的指标确定具体字段（如 prompt_tokens/completion_tokens/cost_estimate）。

3. §6 Resource 类型 11 种——skill/tool/mcp/expert_agent 四种"可执行资源"的调用前检查和调用流程是否需要差异化（当前为统一契约），还是统一足够。

4. §7 确定性转换的 rollback_supported 和 idempotency_note——实际工程中回滚能力取决于 Workspace 快照策略而非转换器自身。是否将 rollback 从 Resource 字段移至 Workspace/Execution Session 层。
```
