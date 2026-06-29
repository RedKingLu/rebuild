---
name: source-materializer
description: P0 接入阶段 — 将项目源码从各类来源（ZIP/Git/GitHub/local_dir/manual）materialize 到 Workspace source/ 目录并生成索引
metadata:
  series: P
  phase: P0
  category: stage_skill
  status: planned
  source: rebuild_self
  license: rebuild-internal
---

# source-materializer（源码导入与 materialization）

## 适用阶段与触发条件

- 阶段：P0 接入。项目创建后的第一道 materialization 工序。
- 触发：Project.source_config 已填写但 source/ 目录为空或需要重新导入。
- 目标：将源码安全、可追溯地导入到 `工作区/projects/{id}/source/`，生成 source_index，为 P1 全量识别提供文件事实底座。

## 输入

- Project.source_type 与 source_config。
- Project.project_id 与 workspace_path。
- 对于 ZIP：已上传的文件路径或 upload_ref。
- 对于 Git/GitHub：remote_url / clone_url、branch、git_host_id（若有）。
- 对于 local_dir：source_config 中的本地路径。
- 对于 manual：空（source/ 保持空）。

## 执行步骤

1. **预检查**：验证 source_config 完整性；检查 workspace source/ 目录是否已存在内容（若存在且非空，记录并询问是否覆盖）。
2. **按 source_type 执行导入**：
   - ZIP：读取已解压目录（`data_dir/project-sources/{id}/`），用安全库函数递归复制到 `workspace/source/`，过滤隐藏目录和大文件。
   - Git：调用受控 Git service（`git clone --depth 1 --branch {branch} {remote_url} {target}`），timeout 120s，路径经 `_guard` 检查。
   - GitHub：同 Git，使用 clone_url。
   - local_dir：`shutil.copytree` 或只读引用（依 source_config.path 是否在 workspace 边界内）。
   - manual：跳过导入，source/ 保持空，诚实记录。
3. **文件过滤**：跳过 `.git/`、`node_modules/`、`vendor/`、`__pycache__/`、`.venv/` 等；>10MB 文件标记 `too_large`；二进制文件标记 `binary` 不读取内容。
4. **生成 source_index**：遍历 source/ 目录树，生成结构化 JSON 索引（文件路径、类型、大小、修改时间），写入 `artifacts/source_index.json`。
5. **错误处理**：导入失败写入 Evidence Gap；部分成功记录 warnings；所有动作写 Trace；L3+ 动作（Git clone 网络访问）写 Audit。

## 输出 / 产物（Artifact / Evidence）

- source_index.json（Artifact）：文件/目录全量索引。
- import_result.json（Artifact）：导入方法、成功/失败/警告、文件数、过滤项。
- Evidence candidates：source_accessible、workspace_source_populated、source_index_created。
- 导入失败项写入 Evidence Gap。

## 质量门 / 验收标准

- source/ 目录非空（manual 类型除外）且文件可读。
- source_index 覆盖所有未过滤文件。
- 无路径穿越（所有路径经 `_guard` / `commonpath` 检查）。
- 无明文凭据泄露（导入过程中不读取 .env/credential 文件内容）。
- 所有动作有 Trace，L3+ 有 Audit。

## 禁止

- 禁止修改、格式化、重命名源文件。
- 禁止在终端中以交互式 shell 脚本为主要实现——后端 service 封装，终端仅展示日志。
- 禁止静默覆盖已存在的 source/ 内容。
- 禁止在 Trace/Audit 中记录文件内容（仅记录路径和元数据）。
