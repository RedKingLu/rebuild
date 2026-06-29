# R9-3A 开工交接提示词：R9-2 收口修订 + 地基补齐与 Source Materialization

文件身份：新窗口 / 本地施工 Agent 一次性交接提示词  
适用项目：rebuild  
项目路径：`/home/king/rebuild/`  
当前阶段：R9-3A 地基补齐 + source materialization + 目录/索引 + 状态骨架  
任务类型：施工阶段 / 分轮施工第 1 轮 / 必须先完成 R9-2 收口修订  
输出原则：报告数量尽量少；默认输出 1 份 `R9-3A-施工收口报告.md`，施工过程中在进度追踪中即时更新；如证据很多，最多额外输出 1 份 `R9-3A-验收证据附录.md`。

---

## 0. 启动自检

我正在执行 rebuild 当前版本 R9-3A 施工任务。我不能依赖旧窗口记忆，不能假设前面阶段已经完整完成，也不能假设后续阶段会替我补救。

本轮必须先完成 R9-2 收口修订，再进入 R9-3A 施工。R9-2 已完成 R9-1R 收口、历史吸收、三模式自由切换、Auto 授权代理、source materialization 边界、Trace/Audit 失败处理、R9-3A~D 分轮施工计划等方案完善，但审核指出仍需修订以下问题：

1. 澄清 4 个 Skill 文件是否已实际落盘；若已落盘，登记为“资源占位已写入 / planned / 未注册 / 未接线”。
2. 删除所有固定 pytest 数量门槛，统一为“后端全量 pytest 0 failed + 关键链路测试覆盖”。
3. 将 P1 全量识别验收从“14 项覆盖 ≥12 项”改为“14 项全部覆盖；不适用项标记 not_applicable；无法判断项生成 Evidence Gap”。
4. 补充历史吸收记录的具体路径、字段、函数或反例路径；若本轮暂不深挖，必须登记到 R9-2 收口缺口，不得伪装完整吸收。
5. 压缩 R9-3 子轮文档数量，避免每个子轮默认三份文档造成交接碎片化。
6. 核实 D-079~D-082 是否实际登记到 `01-决策记录.md`；未登记则不得写“已登记”。
7. 将 Q-R9-2-01 收敛为默认策略：R9-3C 先做文件级全量识别 + 规则匹配；若 Review 发现置信度不足，再触发 AI/轻量 AST 辅助作为返工增强；除非用户另行要求，不作为当前开放待确认项。

完成这些收口修订后，才能进入 R9-3A 施工。

---

## 1. 必读事实源

执行前必须读取。若路径不存在，以本地实际路径为准，并在报告中说明。

### 1.1 硬规则与工作准则

- `/home/king/rebuild/AGENTS.md`
- `/home/king/rebuild/skills/R-建设执行/rebuild-work-guidelines/SKILL.md`

### 1.2 R9 方案材料

- `R9-1-P0-P1最小真实链路自有方案.md`
- `R9-1R-P0-P1小循环与全量识别方案返工报告.md`
- `R9-2-历史吸收与P0-P1方案完善报告.md`

注意：R9-2 报告可作为方案基础，但必须先完成本提示词第 2 节的收口修订。

### 1.3 项目治理与进度追踪

- `/home/king/rebuild/文档/00-项目治理/00-项目总览.md`
- `/home/king/rebuild/文档/00-项目治理/01-决策记录.md`
- `/home/king/rebuild/文档/00-项目治理/02-术语表.md`
- `/home/king/rebuild/文档/00-项目治理/06-R阶段总计划.md`
- `/home/king/rebuild/证据/进度追踪/01-功能表.md`
- `/home/king/rebuild/证据/进度追踪/02-阻塞项.md`
- `/home/king/rebuild/证据/进度追踪/03-待确认项.md`
- `/home/king/rebuild/证据/进度追踪/04-施工记录.md`
- `/home/king/rebuild/证据/进度追踪/05-验收与证据索引.md`
- `/home/king/rebuild/证据/进度追踪/06-状态口径与更新规则.md`

### 1.4 当前代码事实源

必须读取实际代码，不得只看报告：

- `backend/app/models/project.py`
- `backend/app/models/run.py` 或实际 Run 模型位置
- `backend/app/models/coding_agent_config.py`
- `backend/app/models/skill_definition.py`
- `backend/app/api/routes_projects.py`
- `backend/app/api/routes_workspace.py`
- `backend/app/api/routes_imports.py`
- `backend/app/services/project_service.py`
- `backend/app/services/workspace_service.py`
- `backend/app/services/execution_provider.py`
- `backend/app/services/git_oauth_service.py` / Git 相关 service，如存在
- `frontend/src/pages/workspace/WorkspacePage.tsx`
- `frontend/src/pages/workspace/BottomDock.tsx`
- `frontend/src/pages/workspace/TerminalPanel.tsx`
- `frontend/src/services/workspaceService.ts`
- `frontend/src/services/projectService.ts`
- `source/skills/p0/`
- `source/skills/p1/`
- `source/skills/common/`
- `工作区/`

---

## 2. R9-2 收口修订任务

进入施工前先完成以下文档/状态收口。若某项需要修改正式文档，直接修改并记录；若无法修改，登记阻塞，不得跳过。

### 2.1 Skill 文件状态核实

核查以下 Skill 是否已实际落盘：

- `source/skills/p0/source-materializer/SKILL.md`
- `source/skills/p0/intake-review/SKILL.md`
- `source/skills/p1/full-stack-profiler/SKILL.md`
- `source/skills/p1/profile-review/SKILL.md`

处理规则：

- 若已落盘：在 R9-2 收口记录中标记为“资源占位已写入 / planned / 未注册到 SkillDefinition / 未接线”。
- 若未落盘：将 R9-2 报告中的“已创建”修订为“拟创建”。
- 不得写“未施工”同时又写“已创建文件”而不解释。

### 2.2 测试门槛修订

全仓搜索并修订 R9 相关报告中的固定测试数量门槛，例如：

- `pytest ≥ 180`
- `pytest ≥ 195`
- `14 项识别覆盖 ≥12 项`

统一改为：

- 后端全量 pytest 0 failed；允许 skipped，但需说明。
- 前端 build exit 0。
- 新增测试覆盖 R9-3A/B/C/D 关键链路。
- Playwright 或等价浏览器级证据覆盖关键 UI/API 链路。
- 测试数量可记录为事实，但不得作为唯一验收门槛。

### 2.3 全量识别验收修订

将“14 项识别覆盖 ≥12 项”修订为：

- 14 项必须全部处理。
- 不适用于当前项目的项标记为 `not_applicable` 并说明理由。
- 信息不足无法判断的项生成 Evidence Gap。
- 不得以“达到 12 项”代替全量识别基线。

### 2.4 历史吸收细节修订

R9-2 历史吸收记录偏概括。补充：

- 具体文件路径。
- 关键字段、函数、组件、端点或失败反例位置。
- 吸收 / 不吸收 / 适配方式 / 风险。
- 对 R9-3A/B/C/D 哪一轮有影响。

如果短时间内无法补全所有历史细节，必须诚实标记为 R9-2 收口缺口，并明确 R9-3A 不依赖该缺口，不能伪装为“吸收完整”。

### 2.5 R9-3 文档数量压缩

不要默认每个子轮三份文档。采用少量滚动文档：

- `R9-3-分轮施工计划.md`：包含 R9-3A/B/C/D 的起始计划、输入、输出、验收标准。
- `R9-3-分轮施工记录.md`：滚动记录 R9-3A/B/C/D 的施工过程、改动、问题、决策。
- `R9-3-分轮施工收口报告.md`：R9-3 全量施工完成后输出。
- `R9-4-独立验收报告.md`：R9-4 独立验收输出。

如确需为某个子轮单独输出文件，必须说明必要性。

### 2.6 决策登记核实

核实 D-079、D-080、D-081、D-082 是否实际登记到 `01-决策记录.md`。

规则：

- 已登记：报告列出具体位置和状态。
- 未登记：本轮补登记或改为“决策草案待登记”。
- D-080/D-081/D-082 如果尚未经用户明确 accepted，保持 proposed，不得擅自改 accepted。

### 2.7 待确认项收敛

Q-R9-2-01 不再作为开放待确认项。默认策略：

- R9-3C 先做文件级全量识别 + 规则匹配。
- 若 Review 发现 confidence 不足，再触发 AI 辅助或轻量 AST 解析作为返工增强。
- 深度兼容性判断优先归 P2，除非用户另行要求 R9 提前做。

更新 `03-待确认项.md`，不要保留无必要的开放式问题。

---

## 3. R9-3A 施工目标

R9-3A 是 R9 分轮施工第 1 轮，目标是完成 P0/P1 链路施工前的基础补齐：

1. R8 P2 小项修复。
2. D-078 的 `Project.coding_agent_ref` 字段和持久化。
3. Source Materializer service。
4. source_index 生成。
5. 三模式自由切换的数据模型和最小 API 骨架。
6. Run 服务对 execution_mode / run_goal / objective 的真实读取。
7. P0/P1 Skill 资源状态整理、注册或 planned 标记。
8. 进度追踪和治理文档同步。

R9-3A 不实现完整 P0 向导、不实现 P1 全量识别、不实现三模式完整行为。那些分别归 R9-3B、R9-3C、R9-3D。

---

## 4. R9-3A 工作包

### WP-A0：开工前状态复核

必须做：

1. 读取事实源。
2. 运行后端 pytest 当前基线。
3. 运行前端 build 当前基线。
4. 核查 R9-2 收口修订完成情况。
5. 在 `R9-3-分轮施工记录.md` 创建 R9-3A 记录段。
6. 若发现阻塞，登记到 `02-阻塞项.md`。

完成条件：状态复核清楚，未登记阻塞不得继续施工。

### WP-A1：R8 P2 小项修复

修复项：

1. 终端 cwd 非项目工作区。
   - 终端命令执行时 cwd 应为 `工作区/projects/{project_id}/` 或更精确的受控 workspace path。
   - `pwd` 或等价命令应返回项目工作区路径。

2. outputHistory 死态。
   - TerminalPanel 执行结果推送到 outputHistory。
   - 输出 tab 能展示历史执行摘要。

3. `_guard` 前缀安全。
   - 使用 `os.path.commonpath` 或等价方式加固。
   - 路径穿越和前缀伪匹配必须拒绝。

4. persistence 标记诚实性。
   - 根据实际是否落盘动态标记。
   - project_id 为空、未落盘时不得标 file+memory。

5. envelope meta source_status 诚实化。
   - 已真实化端点标记 real。
   - mock/partial/not_connected 不得写成 real。

完成条件：对应测试覆盖，后端全量 pytest 0 failed。

### WP-A2：D-078 `Project.coding_agent_ref` 持久化

必须做：

1. Project 模型新增 nullable `coding_agent_ref` 或等价字段。
2. Alembic 迁移。
3. Project schema / update schema 支持该字段。
4. Project API 支持读取和更新该字段。
5. Workspace CodingAgentSelector 初始化时读取 Project 当前选择。
6. 用户切换 Coding Agent 时持久化到后端。
7. UI 诚实标注：真实 AI 编码调用归 R11，R9 仅完成选择持久化。

完成条件：刷新页面后选择仍保留；无伪装真实 AI 编码调用。

### WP-A3：Source Materializer service

必须作为后端 service / tool capability 实现，不以终端脚本为主。

覆盖 source_type：

1. `zip`
   - 从现有上传/解压位置复制或移动到 `工作区/projects/{id}/source/`。
   - 使用安全库函数。
   - 防止 zip slip / 路径穿越。

2. `git`
   - 受控 clone 到 `source/`。
   - 支持 branch / shallow clone，如已有 source_config。
   - timeout、脱敏、路径 guard。

3. `github`
   - 通过已有 GitHub/OAuth source_config 获取 clone_url 或等价信息。
   - clone 到 `source/`。
   - 凭据不得进入日志、Trace、Audit 明文。

4. `local_dir`
   - 根据 source_config.path 复制、引用或只读同步到 `source/`。
   - 必须路径校验，禁止越界和敏感目录误导入。

5. `manual`
   - source/ 保持空，生成诚实 Evidence Gap。

统一要求：

- 导入动作写 Trace。
- L2+ 或外部影响动作写 Audit。
- 失败生成 Evidence Gap。
- 过滤 `.git/`、`node_modules/`、`vendor/`、`.venv/`、`__pycache__/` 等高噪声目录，或在 source_index 中标记为 skipped。
- 大文件、二进制文件标记，不读取内容。

完成条件：至少 zip/manual/local_dir 可测；git/github 若缺凭据，可用 mock-free 的失败路径和 Evidence Gap 验证，但不得伪装成功。

### WP-A4：source_index 生成器

必须生成 `artifacts/source_index.json` 或 `indexes/source_index.json`。推荐用户可见主索引放 `artifacts/source_index.json`，平台内部索引可放 `indexes/`。

source_index 至少包含：

- file_count。
- directory_count。
- top_level_dirs。
- key_files。
- skipped_dirs。
- binary_files_count。
- too_large_files_count。
- generated_at。
- source_type。
- materialization_status。

完成条件：source materialization 后自动生成；文件树更新后可重新生成。

### WP-A5：三模式自由切换数据模型与最小 API 骨架

R9-3A 只做骨架，不做完整三模式行为。

必须做：

1. Workspace / Project / Run 层明确当前 mode 存储位置。
2. ModeChangeEvent 或等价事件记录结构。
3. API：读取当前 mode。
4. API：切换 mode。
5. 切换写 Trace；授权密度变化写 Audit 或至少预留 Audit 调用。
6. 前端 ExecModeSwitch 不再只是本地 Zustand 状态，应调用后端切换 API。
7. 当前执行中不可切换时返回 pending 或明确错误/提示。

完成条件：切换后刷新页面仍保持；Trace 可见 mode_change。

### WP-A6：Run 服务读取 execution_mode / run_goal / objective

必须做：

1. Run create/update 服务真实读取 schema 中的 execution_mode / run_goal / objective。
2. 不再只停留在 schema 字段。
3. 写入 Run 状态或等价存储。
4. Workspace 顶部状态可展示当前 Run 的 mode/goal。

完成条件：创建 Run 后 API 可读回 mode/goal。

### WP-A7：Skill 资源状态整理与注册计划

必须做：

1. 确认 4 个新 Skill 文件是否已落盘。
2. 若已落盘，校验 SKILL.md 内容质量。
3. 决定本轮是否注册到 SkillDefinition 表。
4. 若注册，必须标记 status=planned 或 active，并说明是否接线。
5. 不得写“active”但未被上下文组装/Skill loader 使用。

完成条件：Skill 状态诚实，无 planned/active 混淆。

### WP-A8：文档与进度追踪同步

必须做：

1. 更新 `04-施工记录.md`，记录 R9-3A 施工。
2. 更新 `05-验收与证据索引.md`，登记 R9-3A 证据。
3. 如新增/修复阻塞，更新 `02-阻塞项.md`。
4. 如收敛待确认，更新 `03-待确认项.md`。
5. 如修改决策 D-079~D-082，更新 `01-决策记录.md`。
6. 如阶段快照仍过时，更新 `00-项目总览.md` / `02-术语表.md`，或登记为 R9-3A 文档同步缺口。

完成条件：代码完成但追踪未更新，不得视为完成。

---

## 5. R9-3A 验收标准

R9-3A 完成当且仅当：

1. R9-2 收口修订完成。
2. 后端全量 pytest 0 failed。
3. 前端 build exit 0。
4. 终端 cwd 在项目工作区。
5. outputHistory 能接收终端执行结果。
6. `_guard` 前缀安全已加固并有测试。
7. persistence 标记诚实。
8. `Project.coding_agent_ref` 可持久化。
9. Coding Agent 选择器刷新后保留选择。
10. Source Materializer service 存在，不以终端脚本为主。
11. zip/manual/local_dir 路径至少有实测；git/github 有受控设计和可测失败路径。
12. source_index 可生成并在 Workspace 可见或可通过 API 获取。
13. 三模式切换 API 骨架存在，切换写 Trace。
14. ExecModeSwitch 调后端，不再只是本地状态。
15. Run service 真实读取 execution_mode / run_goal / objective。
16. Skill 资源状态诚实。
17. 进度追踪已同步。
18. 无 Key/Token/Secret/.env 明文泄露。

---

## 6. R9-3A 输出要求

默认只输出：

- `R9-3A-施工收口报告.md`

如果证据较多，可额外输出：

- `R9-3A-验收证据附录.md`

同时滚动更新：

- `R9-3-分轮施工计划.md`
- `R9-3-分轮施工记录.md`

不得默认为每个子轮输出三份独立报告。

施工收口报告必须包含：

1. 开工前状态复核。
2. R9-2 收口修订结果。
3. 各工作包完成情况。
4. 代码改动清单。
5. 测试与构建结果。
6. 实测证据摘要。
7. 进度追踪更新清单。
8. 未完成项和原因。
9. 是否建议进入 R9-3B。

---

## 7. 安全红线

1. 不得泄露 Key/Token/Secret/Password/.env 明文。
2. source materialization 日志、Trace、Audit 必须脱敏。
3. Git/GitHub clone 凭据不得出现在 stdout/stderr/Trace/Audit。
4. ZIP 解压必须防 zip slip。
5. local_dir 导入必须防路径越界和敏感目录误导入。
6. source/ 默认只读。
7. Auto 模式不得代理 L4+ 或阶段 Gate。
8. 施工 Agent 不得自称完成独立验收。

---

## 8. 禁止事项

1. 禁止未完成 R9-2 收口修订就直接施工。
2. 禁止用终端脚本代替 source materialization 后端能力。
3. 禁止把 Skill planned 写成 active。
4. 禁止把三模式切换继续做成本地 UI state。
5. 禁止写死测试数量作为通过标准。
6. 禁止 14 项识别继续使用 ≥12 项口径。
7. 禁止代码完成但进度追踪不更新。
8. 禁止施工 Agent 自己出具独立验收结论。

---

## 9. 最终提醒

R9-3A 是 R9 分轮施工的地基补齐轮。它不需要完成 P0 向导和 P1 全量识别，但必须把后续 P0/P1 小循环依赖的基础状态、source materialization、mode 切换、Run 字段、D-078 持久化、R8 P2 小项修复、Skill 状态和追踪同步打牢。否则 R9-3B/C/D 会继续返工。
