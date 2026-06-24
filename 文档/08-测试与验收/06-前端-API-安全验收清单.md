# 06-前端-API-安全验收清单

> 文档路径：`文档/08-测试与验收/06-前端-API-安全验收清单.md`
> 文档状态：**accepted**
> 平台名称：rebuild
> 当前版本：V26.1.1
> 来源草稿：`产物/草稿/06-前端-API-安全验收清单.md`（v0.1）
> 本次处理人 / Agent：Claude (deepseek-v4-pro) / R1 文档正式化流程
> 最后更新时间：2026-06-24
> 审核状态：经用户审核通过（2026-06-24）
> 修订说明：R2 去重(Q5=B)：样板块补引用 AGENTS§18/01-决策记录，收敛通用复述，安全红线就地保留。
> 文档定位：定义 rebuild 当前版本前端、API 与安全三类横切能力的验收清单。覆盖前端真实能力标记、P0-P6/R 阶段展示、Workspace、Gate/Evidence/Trace/Audit 前端展示、API 字段/错误码/SSE、状态恢复、安全脱敏、Policy/Hook/Authorization、模型与资源能力标记、回归检查、缺陷分级。
>
> **本目录关联文档**：
> - `00-测试与验收总览.md` — 测试与验收体系总入口（本文是其 §11-§13 的详述）
> - `03-P0-P6验收清单.md` — P0-P6 阶段检查清单（本文是其前端/API/安全维度的横切补充）
> - `05-Artifact-Evidence-Trace-Audit验收规范.md` — AETA 对象验收规范（本文 §6 是其前端展示维度的验收）
>
> 上级依据：`文档/00-项目治理/01-决策记录.md`、`文档/05-API与集成契约/`、`文档/06-UX与前端/`、`文档/07-安全与权限/`。
>
> 重要边界：本文是前端、API、安全验收清单，不替代前端设计规范、API 实现契约、安全策略、测试用例或自动化测试实现。本文为 R1 建议契约，R2 需按正式文档体系校准；R3/R4/R5/R6/R8/R9-R17 需按真实实现和联调结果校准。

---

## 0. 编写原则

本文遵守文档事实源层级（`文档地图.md` §1）：

```text
项目治理 > 产品定义 > 架构设计 > 专题规范
```

本文只展开前端、API、安全验收清单，不重新定义上级事实。

本文必须遵守：

```text
1. R3 可先建设前端主干与体验壳，允许使用静态数据或 mock API，但必须显式标记未接入真实服务的能力；
2. API 字段 R1 输出建议契约，R2 校准为实现契约；
3. 前端与 API 验收不得把 mock、静态演示、未接真实服务显示为真实能力；
4. P0-P6 是用户项目可裁剪产品流程，R0-RN 是平台自身建设阶段，不得混用；
5. 当前版本不设独立 Mission 产品层（不引入 Mission，D-070）；
6. Gate、Evidence Gap、Trace 缺失、Audit 缺失必须前端可见；
7. No Evidence / No Trace, No Trusted Result；
8. 密钥与配置必须脱敏（不泄露，D-032）；
9. 前端、API 响应、SSE payload、日志、Trace、Audit、截图、导出不得泄露 Key / Token / Secret / Password（D-032）；
10. L5 高风险强制用户 Gate（D-034，详见 02-术语表 第七部分）；
11. 容器、终端或前端 UI 不能作为状态源；
12. （P 阶段晋级 Gate、Policy 优先于 Agent、Authorization Agent 不得批准 Policy 禁止项、Artifact 不自动晋升、Manual/Plan/Auto 边界等通用红线详见 AGENTS §18 + 01-决策记录）。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

**本文特有禁止项**（通用禁止事项详见 AGENTS.md §18）：

```text
1. 不得将前端能点开等同于真实能力可用；
2. 不得将 API 返回 200 等同于业务完成；
3. 不得将 SSE completed 事件等同于阶段 completed；
4. 不得将浏览器缓存、前端本地状态或终端输出作为事实源；
5. 不得将 Gate 显示为普通 toast 后继续执行。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 1. 总体验收口径

前端、API、安全验收应共同确认：

```text
1. 用户看到的状态与后端事实源一致；
2. API 字段语义与文档一致；
3. SSE 事件只作为刷新提示，不作为唯一状态源；
4. Gate / Evidence / Trace / Audit 状态可查询、可展示、可恢复；
5. mock / not_connected / static_demo 显式标记（详见 00-测试与验收总览 §19）；
6. 安全、脱敏、Policy、Audit、Gate 链路不可绕过；
7. 错误响应可被前端正确解释；
8. 状态恢复后不出现误导；
9. 验收报告可复核；
10. 不泄露密钥。
```

验收对象包括：

```text
前端页面；
前端状态管理；
API 响应；
API 错误码；
SSE 事件；
后端状态查询；
Gate / Authorization；
Artifact / Evidence / Trace / Audit；
安全与脱敏；
模型与资源能力标记；
状态恢复。
```

---

## 2. 前端真实能力标记清单

必须检查：

```text
mock 是否显式标记；
static_demo 是否显式标记；
not_connected 是否显式标记；
read_only 是否显式标记；
requires_gate 是否显式标记；
blocked_by_policy 是否显式标记；
credential_missing / credential_invalid 是否显式标记；
redaction_required 是否显式标记；
real_available 是否只用于真实接入能力。
```

通过条件：

```text
1. 所有未接真实服务能力均有可见标记；
2. 静态数据不被写成后端真实结果；
3. mock SSE 不被写成真实事件；
4. 示例数据不进入 Evidence validated；
5. 用户能区分体验壳和真实能力；
6. R17 前未替换 mock 的能力仍显式标记。
```

阻断红线：

```text
mock 被显示为真实；
未接服务被显示为可执行；
示例 Evidence 被显示为 validated；
静态 Gate 被显示为已授权；
前端本地状态伪造 completed。
```

---

## 3. P0-P6 与 R 阶段展示清单

必须检查：

```text
P0-P6 是否显示为用户项目流程；
R0-RN 是否显示为平台建设阶段；
P 阶段和 R 阶段是否未混用；
被裁剪 P 阶段是否显示 not_enabled / not_applicable；
启用 P 阶段是否显示 Gate / Artifact / Evidence / 完成条件；
P 阶段晋级 Gate 是否可见；
P5 验证状态是否突出 Evidence；
P6 交付是否不暗示增强能力全部完成。
```

通过条件：

```text
1. 阶段身份清晰；
2. 阶段状态来自后端查询；
3. 阶段 completed 有完成依据；
4. Gate 未决策不显示通过；
5. Evidence Gap 不隐藏；
6. 旧 Phase / 旧 F0-F6 / 旧 S0-S7 不作为主流程展示。
```

---

## 4. Workspace 与材料视图验收清单

必须检查：

```text
Workspace 是否从 Project 进入；
Workspace 是否采用独立 IDE 心智；
代码视图与材料视图是否区分；
材料视图是否区分项目文档、交接材料、产物、证据、运行记录、参考资料（详见 02-术语表 第九部分）；
右侧检视是否展示 Evidence / Trace / Audit / Gate；
底部 Dock 是否展示终端、输出、问题、任务进度；
退出 Workspace UI 后任务状态是否可恢复；
Workspace 文件预览和输出是否脱敏。
```

通过条件：

```text
1. Project Workspace 隔离清晰；
2. 文件身份不混淆；
3. Artifact 不被显示为 Evidence；
4. Trace / Audit / Gate 可见；
5. 状态恢复不依赖前端缓存；
6. 敏感文件不展示明文。
```

---

## 5. Gate / Authorization 前端验收清单

必须检查：

```text
active_gate 是否可见；
gate_type 是否可见；
risk_level 是否可见（详见 02-术语表 第七部分）；
risk_reasons 是否可见；
blocking_reasons 是否可见；
policy_check_ref 是否可见或可追踪；
allowed options 是否正确；
Policy blocked 是否不提供批准继续；
Gate 决策后是否刷新后端状态；
Gate 决策是否写 Audit。
```

通过条件：

```text
1. Gate 不被 toast 化；
2. Gate 未决策动作暂停；
3. L5 必须用户 Gate；
4. P 阶段晋级 Gate 必须用户授权；
5. Gate 决策 Audit 可查询；
6. 前端不本地伪造 Gate resolved。
```

---

## 6. Artifact / Evidence / Trace / Audit 前端验收清单

必须检查（与 `05-Artifact-Evidence-Trace-Audit验收规范.md` §12 对齐）：

```text
Artifact 身份和状态是否可见；
Evidence candidate / validated 是否区分；
Evidence Gap 是否可见；
Trace 状态是否可见；
Audit 状态是否可见；
对象关联是否可见；
缺口是否有 next_actions；
对象详情是否脱敏；
mock 对象链是否显式标记。
```

通过条件：

```text
1. Artifact 不显示为 Evidence validated；
2. Trace 不显示为 Evidence；
3. Audit 不显示为授权本身；
4. Evidence Gap 不隐藏；
5. Trace / Audit 缺失不隐藏；
6. 右侧检视能支持复核。
```

---

## 7. API 字段验收清单

R1 API 字段为建议契约，R2 校准为实现契约。验收时必须检查：

```text
字段是否存在；
字段类型是否符合文档；
字段是否必填 / 可选清晰；
字段说明是否与实现一致；
字段来源是否明确；
字段是否标注 R2 待校准；
字段是否脱敏；
字段是否可被前端正确使用；
字段缺失是否有错误或降级状态；
字段变更是否登记。
```

通用关键字段：

```text
project_id；
run_id；
stage；
status；
active_gate；
gate_ref；
evidence_refs；
evidence_gap_refs；
trace_ref；
audit_ref；
redaction_status；
credential_status；
risk_level；
policy_check_ref；
error_ref；
request_id。
```

通过条件：

```text
1. 前端依赖字段有稳定来源；
2. 状态字段不为空，未知使用 unknown；
3. 引用字段可查询；
4. redaction_status 可见；
5. credential 字段不返回 Secret；
6. R2 后实现契约与前端联调一致。
```

---

## 8. API 错误码验收清单

必须检查错误码是否覆盖：

```text
policy_blocked；
gate_required；
gate_not_resolved；
evidence_missing；
evidence_gap_blocking；
trace_missing；
audit_missing；
redaction_required；
credential_missing；
credential_invalid；
permission_denied；
state_mismatch；
resource_read_only；
resource_blocked；
validation_failed；
internal_error。
```

错误响应验收项：

```text
是否包含 request_id；
是否包含可追踪 ref；
是否可映射前端文案；
是否给出安全 next_actions；
是否不暴露堆栈；
是否不泄露密钥；
是否不被前端显示为成功。
```

通过条件：

```text
1. 错误码语义稳定；
2. 前端能展示正确状态；
3. gate_required 能关联 Gate；
4. policy_blocked 不展示继续按钮；
5. redaction_required 不返回原文；
6. internal_error 不暴露敏感详情。
```

---

## 9. SSE / Event 验收清单

必须检查：

```text
事件是否触发；
事件名称是否与文档一致；
事件 payload 是否脱敏；
事件是否只作为刷新提示（详见 00-测试与验收总览 §11）；
断线重连是否触发重新查询；
resync_required 是否生效；
mock SSE 是否显式标记；
事件 completed 是否不被直接当作阶段 completed。
```

关键事件示例：

```text
gate_created；
gate_decided；
policy_blocked；
evidence_gap_detected；
trace_written；
audit_written；
redaction_required；
verification_completed；
verification_failed；
resync_required。
```

通过条件：

```text
1. SSE 不作为唯一状态源；
2. 前端收到事件后重新查询对象；
3. payload 不含 Secret；
4. 断线恢复不丢关键状态；
5. mock SSE 明确标记；
6. completed 事件不直接驱动可信完成。
```

---

## 10. 状态恢复验收清单

必须检查恢复来源：

```text
数据库；
LangGraph checkpoint；
workspace 文件；
Trace / Audit；
event log；
后端状态查询。
```

不得作为事实源：

```text
浏览器缓存；
SSE 事件缓存；
终端窗口状态；
容器存在；
前端本地状态；
模型输出。
```

通过条件：

```text
1. 刷新页面后状态一致；
2. 退出 Workspace 后任务状态可恢复；
3. Gate 状态可恢复；
4. Evidence / Trace / Audit 状态可恢复；
5. 后台任务状态可查询；
6. resync_required 能恢复真实状态。
```

---

## 11. 安全与脱敏验收清单

必须检查：

```text
前端不展示 Secret 明文；
API 不返回 Secret 明文；
SSE payload 不含 Secret；
日志不含 Secret；
Trace 不含 Secret；
Audit 不含 Secret；
截图 / 导出不含 Secret；
模型上下文不含 Secret；
Memory 不写入 Secret；
credential_invalid 不返回密钥片段。
```

脱敏状态验收：

```text
redaction_required；
redacted；
redaction_failed；
credential_missing；
credential_invalid；
permission_denied；
not_scanned；
scan_failed。
```

通过条件：

```text
1. redaction_required 阻止原文展示；
2. redaction_failed 不展示原文；
3. not_scanned 不显示安全；
4. scan_failed 不显示无敏感信息；
5. mock redaction 显式标记；
6. 导出前执行脱敏检查。
```

---

## 12. Policy / Hook / Authorization 验收清单

必须检查：

```text
Hook 是否在动作前触发；
Policy Check 是否在执行前完成；
Policy blocked 是否不可继续；
unknown / error 是否不默认 allowed；
Authorization Agent 是否只做辅助建议；
Authorization Agent 是否不批准 Policy 禁止项；
风险升级是否触发 Gate；
高风险动作是否 Audit；
L5 是否强制用户 Gate（详见 02-术语表 第七部分）。
```

通过条件：

```text
1. Policy 优先于 Agent；
2. 前端无绕过 Policy 的入口；
3. API 无绕过 Gate 的入口；
4. Auto Mode 不静默执行高风险动作；
5. Policy blocked 有清晰前端文案；
6. 相关 Trace / Audit 可查。
```

---

## 13. 模型与资源能力验收清单

模型检查：

```text
Provider / Profile / Binding 是否可查询；
模型调用是否有 Trace；
Provider Key 是否脱敏；
Fusion 是否作为模型能力展示；
Fusion 是否不绕过 Policy / Gate / Audit；
usage / cost 不可用是否不显示为零成本。
```

资源检查：

```text
Resource Registry 是否可查询；
Skill / Tool / MCP / Expert Agent 是否区分；
Case / Knowledge 是否只读；
社区资源是否默认只读参考；
资源调用是否记录来源、风险、权限、Trace；
高风险资源是否 Gate / Audit；
确定性转换 dry-run / apply 是否区分。
```

通过条件：

```text
1. active / connected 不等于可直接执行；
2. read_only 不显示执行按钮；
3. 社区资源不默认可执行；
4. 资源输出不自动成为 Evidence validated；
5. 资源凭据不展示；
6. mock 资源显式标记。
```

---

## 14. 前端回归验收清单

每次前端变更后检查：

```text
Project 列表与详情；
Workspace 入口；
P0-P6 阶段展示；
后台任务面板；
Gate 卡片；
文件面板双视图；
右侧 Evidence / Trace / Audit 检视；
底部输出 / 问题 / 任务进度；
模型与资源能力标记；
错误和空状态；
脱敏展示；
刷新与恢复。
```

通过条件：

```text
1. 页面刷新后状态一致；
2. mock 标记不丢失；
3. Gate 不被 toast 化；
4. Evidence Gap 不隐藏；
5. 状态变更不依赖本地假数据；
6. 不泄露密钥。
```

---

## 15. API 回归验收清单

每次 API 变更后检查：

```text
契约字段是否兼容；
错误码是否兼容；
SSE 事件是否兼容；
引用对象是否可查询；
分页 / 过滤 / 排序是否稳定；
状态恢复是否稳定；
脱敏是否仍生效；
前端是否无需猜测字段含义。
```

通过条件：

```text
1. 破坏性变更已登记；
2. 前端适配完成；
3. 错误响应可展示；
4. request_id / trace_ref 可追踪；
5. 不返回 Secret；
6. R2 后契约文档同步更新。
```

---

## 16. 安全回归验收清单

每次安全相关变更后检查：

```text
Policy blocked；
Gate required；
redaction_required；
credential_missing；
credential_invalid；
L5 high risk；
external write；
Git remote write；
Tool / MCP high risk；
Audit missing；
Trace missing。
```

通过条件：

```text
1. 高风险路径不可静默通过；
2. 密钥不可泄露；
3. Policy 不可绕过；
4. Gate 决策可审计；
5. 状态恢复后阻断仍存在；
6. 前端文案不误导。
```

---

## 17. 验收报告要求

前端、API、安全验收报告建议包含：

```text
验收范围；
验收环境；
前端页面清单；
API 端点清单；
SSE 事件清单；
安全场景清单；
通过项；
失败项；
缺陷分级（详见 00-测试与验收总览 §15）；
截图或引用；
request_id / trace_ref；
Gate / Evidence / Audit 状态；
脱敏检查结论；
mock / not_connected 清单；
结论；
待确认项；
下一步建议。
```

规则：

```text
1. 报告不得包含密钥；
2. 报告不得把 mock 写成真实；
3. 报告不得隐藏 P0/P1 缺陷；
4. 报告不得把 API 200 写成业务完成；
5. 报告必须可供独立验收 Agent 复核（详见 00-测试与验收总览 §17）。
```

---

## 18. R2 / R3 / R4 / R5 / R6 / R8 / R9-R17 校准项

### 18.1 R2 文档校准

```text
1. 本文是否对齐最新决策记录；
2. API 字段建议是否与 API 契约目录一致；
3. 前端验收项是否与 UX 文档一致；
4. 安全验收项是否与安全与权限目录一致；
5. 是否仍无 Mission 产品层；
6. 是否重复上级事实，需要改为引用。
```

### 18.2 R3 前端主干校准

```text
1. 体验壳是否显式标记 mock；
2. 阶段、Workspace、文件面板、右侧检视是否可用；
3. Gate / Evidence / Trace / Audit 是否可见；
4. 错误和空状态文案是否不误导；
5. 前端是否未伪造真实能力。
```

### 18.3 R4 API 与工程骨架校准

```text
1. API 实现契约是否形成；
2. 错误码是否接入；
3. SSE 是否接入；
4. 状态恢复是否接入；
5. request_id / trace_ref 是否可查。
```

### 18.4 R5 模型网关校准

```text
1. ModelGateway API 是否真实接入；
2. Provider Key 是否脱敏；
3. 模型调用 Trace 是否可见；
4. usage / cost 状态是否准确；
5. Fusion 能力标记是否不误导。
```

### 18.5 R6 Agent / Skill / 资源基础校准

```text
1. Resource Registry 是否可查询；
2. Skill / Tool / MCP / Expert Agent 是否区分；
3. 资源权限和风险是否展示；
4. 资源调用 Trace / Audit 是否接入；
5. 社区资源是否默认只读参考。
```

### 18.6 R8 Workspace 真实化校准

```text
1. Workspace 状态是否可恢复；
2. 文件预览与写盘是否真实；
3. 执行输出是否脱敏；
4. Gate / Evidence / Trace / Audit 右侧检视是否真实接入；
5. 浏览器缓存是否不作为事实源。
```

### 18.7 R9-R12 主链路校准

```text
R9：P0-P1 前端、API、安全验收；
R10：P2-P3 前端、API、安全验收；
R11：P4 执行链路前端、API、安全验收；
R12：P5-P6 验证交付前端、API、安全验收。
```

### 18.8 R13-R17 增强与发布校准

```text
R13：Fusion 前端 / API / 安全验收；
R14：远程资源调用前端 / API / 安全验收；
R15：社区功能前端 / API / 安全验收；
R16：文档功能前端 / API / 安全验收；
R17：全量联调、体验打磨与发布冻结验收。
```

---

## 19. 前端 / API / 安全验收红线

```text
1. 不得将前端能点开等同于真实能力可用；
2. 不得将 API 返回 200 等同于业务完成；
3. 不得将 SSE completed 事件等同于阶段 completed；
4. 不得将浏览器缓存、前端本地状态或终端输出作为事实源；
5. 不得将 Gate 显示为普通 toast 后继续执行；
6. 不得将 Policy blocked 提供为可批准继续；
7. 不得隐藏 Evidence Gap、Trace 缺失、Audit 缺失；
8. 不得将 Artifact / 模型输出 / 资源输出显示为 Evidence validated；
9. 不得将 accepted_with_risk 显示为无风险通过；
10. 不得泄露 Key / Token / Secret / Password；
11. 不得将 mock / static_demo / not_connected 显示为真实可用；
12. 不得将 read_only 资源显示为可执行；
13. 不得将 credential_invalid 返回密钥片段；
14. 不得将 usage / cost 缺失显示为零成本；
15. 不得使用旧 Phase / 旧 F0-F6 / 旧 S0-S7 作为当前主流程；
16. 不得引入 Mission 产品层；
17. 不得在长期 API、DB、路由、组件、状态字段中固化 V26.1；
18. 不得把 R1 建议契约伪装为实现契约。
```

> 通用禁止与红线完整总表见 AGENTS.md §18；相关决策详见 01-决策记录.md；术语与风险级别（L0-L5）详见 02-术语表.md。

---

## 20. 本文验收标准

本文达到 R1 基本可用标准，当且仅当：

```text
1. 明确总体验收口径（§1）；
2. 明确前端真实能力标记清单（§2）；
3. 明确 P0-P6 与 R 阶段展示清单（§3）；
4. 明确 Workspace 与材料视图验收清单（§4）；
5. 明确 Gate / Authorization 前端验收清单（§5）；
6. 明确 Artifact / Evidence / Trace / Audit 前端验收清单（§6，引用 05-AETA规范）；
7. 明确 API 字段验收清单（§7）；
8. 明确 API 错误码验收清单（§8）；
9. 明确 SSE / Event 验收清单（§9）；
10. 明确状态恢复验收清单（§10）；
11. 明确安全与脱敏验收清单（§11）；
12. 明确 Policy / Hook / Authorization 验收清单（§12）；
13. 明确模型与资源能力验收清单（§13）；
14. 明确前端、API、安全回归验收清单（§14-§16）；
15. 明确验收报告要求（§17）；
16. 明确 R2-R17 校准项（§18）；
17. 明确前端 / API / 安全验收红线 18 条（§19）；
18. 未新增产品决策、未引入 Mission 产品层、未固化最终实现。
```
