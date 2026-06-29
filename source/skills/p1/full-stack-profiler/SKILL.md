---
name: full-stack-profiler
description: P1 建档阶段 — 全量项目识别基线：14 项系统性扫描（文件/目录/模块/语言/框架/构建/依赖/入口/测试/CI-CD/数据库/中间件/配置/文档），形成 P2-P6 可复用的事实底座
metadata:
  series: P
  phase: P1
  category: stage_skill
  status: planned
  source: rebuild_self
  license: rebuild-internal
---

# full-stack-profiler（全量项目识别）

## 适用阶段与触发条件

- 阶段：P1 建档。P0 接入完成后执行。
- 触发：source_index 就绪、P0 intake_report 已生成。
- 目标：建立后续 P2-P6 可直接复用的全量项目识别基线，不把基础识别留给后面重复做。

## 输入

- source_index.json（来自 P0）。
- P1 Context Package（含 P0 intake_report、Environment Profile、文件树摘要、已有 Artifact/Evidence）。
- 文件读取 API（只读，脱敏）。

## 执行步骤（14 项识别，可分片并行）

### 第一组：结构识别（文件/目录/模块）
1. **文件/目录全量索引**：基于 source_index 补充文件大小分布、目录深度、文件类型分布。
2. **源码目录结构**：识别源码根目录、测试目录、资源目录、文档目录、配置目录、构建输出目录的分层结构。
3. **模块/包结构**：识别多模块/Mono-repo 结构、模块间依赖关系（基于 import/require 声明）。

### 第二组：技术识别（语言/框架/构建）
4. **主要语言识别**：文件扩展名统计 + 项目文件确认（.csproj→C#, pom.xml→Java, package.json→JS/TS, go.mod→Go, requirements.txt/pyproject.toml→Python）。
5. **框架识别**：解析项目文件确定 Web 框架、ORM、消息队列客户端、RPC 框架等。
6. **构建系统识别**：MSBuild/Gradle/Maven/npm/pnpm/yarn/pip/poetry/go build/cmake 等。

### 第三组：依赖与入口识别
7. **依赖文件识别和依赖清单初稿**：解析所有依赖声明文件，提取依赖名+版本，标注信创兼容性初判（兼容/需替换/待查）。
8. **入口文件/启动方式候选**：识别 main 函数、Program.cs、App.xaml、index.js、manage.py、Dockerfile ENTRYPOINT 等入口点。

### 第四组：质量基础设施识别
9. **测试目录和测试框架线索**：识别测试目录（test/、tests/、spec/）、测试框架（JUnit/NUnit/xUnit/pytest/jest）、测试覆盖率配置。
10. **容器文件、部署文件、CI/CD 文件识别**：Dockerfile、docker-compose.yml、k8s YAML、Jenkinsfile、.github/workflows/、.gitlab-ci.yml、Terraform 等。

### 第五组：基础设施与配置识别
11. **数据库、中间件、外部服务配置线索**：识别连接字符串配置、ORM 配置、消息队列配置、缓存配置、外部 API endpoint（仅记录类型和位置，不读取或输出明文凭据）。
12. **配置文件和环境变量文件线索**：识别 .env、appsettings.json、application.yml、web.config 等；**必须脱敏**——只记录文件路径和配置键名，绝不读取或输出敏感值。

### 第六组：文档与交叉验证
13. **文档和 README 识别**：识别 README、CHANGELOG、CONTRIBUTING、docs/ 目录；提取文档摘要。
14. **不确定项、冲突项、缺失项登记**：各项识别中 confidence < high 的写入 Evidence Gap；识别结果互相冲突的写入冲突清单；应存在但未发现的写入缺失清单。

## 输出 / 产物（Artifact / Evidence）

每项识别产出独立 Artifact（JSON 格式，结构化）：
- `tech_stack.json`：语言/框架/构建系统/ORM 矩阵，含 confidence。
- `source_structure.json`：源码目录/模块/包结构。
- `dependency_draft.json`：依赖清单初稿 + 信创兼容性初判。
- `entry_points.json`：入口文件/启动方式候选。
- `test_inventory.json`：测试目录/框架/配置线索。
- `cicd_inventory.json`：容器/部署/CI-CD 文件清单。
- `infra_clues.json`：数据库/中间件/外部服务配置线索（脱敏）。
- `config_inventory.json`：配置文件清单（脱敏，仅键名不含值）。
- `doc_inventory.json`：文档清单与摘要。
- `uncertainty_manifest.json`：不确定项/冲突项/缺失项清单 → 生成 Evidence Gap。
- `profiling_summary.md`：P1 建档摘要（可编辑 Markdown）。
- `p2_input_manifest.json`：P2 输入包索引。

## 质量门 / 验收标准

- 14 项识别至少覆盖 12 项（部分项在简单项目中可能不适用，需说明）。
- 每项识别有 confidence 标记（high/medium/low/uncertain）。
- 配置文件/环境变量文件**零明文凭据泄露**。
- 不确定项非空不代表失败——诚实标记 confidence 比假装确定更重要。
- 所有识别结果可追溯到具体文件（Evidence 链完整）。

## 禁止

- 禁止在识别过程中修改源文件。
- 禁止读取或输出 .env/credential/secret 的实际值。
- 禁止凭经验断言而不引用文件证据。
- 禁止把 confidence=uncertain 的项标记为 confirmed。
- 禁止把模型推断作为 Evidence（模型输出是分析，文件扫描结果才是 Evidence）。
