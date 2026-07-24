"""R6/R7 seed data — initial Agent, Skill, and Resource Registry entries.

Run once on startup if tables are empty.

Skill 口径（R7 修订，2026-06-26）：
- R 系列 3 个：均有真实 SKILL.md（skills/R-建设执行/，置于 source/ 之外与运行期隔离）。
- P 系列 24 个：均有真实 SKILL.md（source/skills/{category}/），来源 ECC（MIT）改写为信创迁移语境。
  原 30 个候选中，6 个非迁移类（benchmark-methodology=市场对标 / content-engine=营销 /
  design-system=UI美学 / config-gc=harness运维 / agent-eval=工具选型 / dashboard-builder=运维监控）
  已剔除，不登记为 P 系列运行 Skill。
"""

from sqlalchemy.orm import Session

from app.models.agent_definition import AgentDefinition, AgentType, AgentCategory, DefinitionStatus
from app.models.skill_definition import SkillDefinition, SkillSeries, SkillCategory, SkillStatus
from app.models.resource_entry import ResourceEntry, ResourceType, SourceType, TrustLevel, RiskLevel, ResourceStatus
from app.models.coding_agent_config import CodingAgentConfig, CodingAgentType, CodingAgentInvokeMode, CodingAgentStatus


AGENT_SEEDS = [
    {
        "agent_type": AgentType.node_worker,
        "category": AgentCategory.system,
        "name": "Node Worker Agent",
        "version": "1.0.0",
        "status": DefinitionStatus.active,
        "responsibilities": "执行 Task Node → 调用 Skill → 调用 ModelGateway → 调用受控 Tool/MCP → 生成节点输出 → 生成 Artifact/Evidence 候选 → 执行自验证 → 提交 Acceptance",
        "forbidden": "自行批准高风险动作 / 自行关闭 Gate / 自行标记阶段 completed / 把模型输出标记为 Evidence validated",
        "model_policy_ref": "system-default",
        "context_recipe": {
            "required_context_layers": ["C0", "C1", "C2", "C3", "C4"],
            "optional_layers": ["C5", "C6"],
            "source_priority": ["项目治理 > 产品定义 > 架构设计 > 专题规范"],
            "max_context_budget": "100k tokens",
        },
        "memory_policy": {
            "allow_write_conditions": ["来源明确", "已确认", "不违反 Policy", "不含敏感明文"],
            "deny_write_categories": ["密钥/Token/env明文", "未确认推断", "临时错误日志"],
            "working_memory_rules": "任务结束后清理或归档必要摘要",
        },
    },
    {
        "agent_type": AgentType.acceptance,
        "category": AgentCategory.system,
        "name": "Acceptance Agent",
        "version": "1.0.0",
        "status": DefinitionStatus.active,
        "responsibilities": "检查 Node Worker 输出 → 检查与 Task Plan/Stage Plan 对齐 → 检查 Artifact/Evidence → 检查 Trace/Audit → 给出结论（accepted / rework_required / retry_required / gate_required / failed）",
        "forbidden": "替代 P5 验证 / 替代用户 Gate / 批准 Policy 禁止项 / 把缺 Evidence 的结果标记为可信完成",
        "model_policy_ref": "system-default",
        "context_recipe": {
            "required_context_layers": ["C0", "C2", "C4"],
            "optional_layers": ["C3", "C5"],
            "source_priority": ["C0", "C2", "C4", "C3", "C5"],
            "max_context_budget": "100k tokens",
        },
    },
    {
        "agent_type": AgentType.auto_review,
        "category": AgentCategory.system,
        "name": "Auto Review / Safety Agent",
        "version": "1.0.0",
        "status": DefinitionStatus.active,
        "responsibilities": "在 Auto Mode 内辅助审核 Stage Plan/Task Plan → 辅助判断风险级别 → 辅助判断是否越界 → 辅助判断是否需要 Gate → 辅助处理安全与授权建议",
        "forbidden": "批准 Policy 禁止项 / 批准 L5 高风险动作 / 绕过用户阶段晋级 Gate / 绕过 Audit",
        "model_policy_ref": "system-default",
        "context_recipe": {
            "required_context_layers": ["C0", "C2", "C4"],
            "optional_layers": ["C3"],
            "source_priority": ["C0", "C2", "C4", "C3"],
            "max_context_budget": "100k tokens",
        },
    },
    {
        "agent_type": AgentType.expert,
        "category": AgentCategory.system,
        "name": "Expert Agent",
        "version": "1.0.0",
        "status": DefinitionStatus.active,
        "responsibilities": "提供特定技术栈或领域专家能力 → 辅助复杂迁移判断 → 辅助安全/性能/兼容性/验证分析 → 辅助确定性转换资源选择 → 辅助解释复杂风险",
        "forbidden": "直接成为最终验收主体 / 直接批准高风险执行 / 直接跳过 P5 Evidence / 直接关闭用户 Gate",
        "model_policy_ref": "system-default",
        "context_recipe": {
            "required_context_layers": ["C0", "C1", "C2", "C3", "C4"],
            "optional_layers": ["C5", "C6"],
            "source_priority": ["C0", "C1", "C2", "C3", "C4", "C5", "C6"],
            "max_context_budget": "100k tokens",
        },
    },
    {
        "agent_type": AgentType.conversation_gate,
        "category": AgentCategory.system,
        "name": "Conversation / Gate Agent",
        "version": "1.0.0",
        "status": DefinitionStatus.active,
        "responsibilities": "与用户交互 → 解释 Gate（原因/风险/选项/影响）→ 收集用户决策 → 将用户决策写回 Gate 流程",
        "forbidden": "伪造用户授权 / 默认批准高风险动作 / 隐藏 Evidence 不足 / 隐藏风险或冲突",
        "model_policy_ref": "system-default",
        "context_recipe": {
            "required_context_layers": ["C0", "C4"],
            "optional_layers": ["C1", "C2"],
            "source_priority": ["C0", "C4", "C1", "C2"],
            "max_context_budget": "100k tokens",
        },
    },
]

# ── 目录路径常量 ──
# _S_DIR：P 系列平台运行期 Skill 素材根（source/ 下，会被平台运行期扫描/加载；派生自 settings.source_dir）。
# _R_DIR：R 系列 = rebuild 自身建设用 Skill，【必须与平台运行期隔离】，故置于 source/ 之外的顶层 skills/
#          （D-008-REV1 隔离原则；D-103 更正：R 系列建设 Skill 不得进入 source/，否则会被 rebuild 平台
#          运行期资源扫描/加载误当作用户项目运行能力）。派生自 source_dir 的父目录（repo 根），不硬编码。
import os as _os
from app.core.config import settings as _settings
_S_DIR = f"{_settings.source_dir}/skills"
_R_DIR = f"{_os.path.dirname(_settings.source_dir)}/skills/R-建设执行"

SKILL_SEEDS = [
    # ── R 系列（rebuild 自身建设，均有真实 SKILL.md）──
    {
        "name": "R-数据库施工规范",
        "series": SkillSeries.R,
        "category": SkillCategory.other,
        "description": "rebuild 特有：SQLAlchemy+Alembic migration 编写、repository 规范、seed/fixture 规范、测试 DB 隔离规范、加密字段规范",
        "status": SkillStatus.active,
        "skill_source": "rebuild_self",
        "directory_path": f"{_R_DIR}/R-数据库施工规范/",
    },
    {
        "name": "R-资源创建登记",
        "series": SkillSeries.R,
        "category": SkillCategory.other,
        "description": "rebuild 特有：Agent/Skill/Tool/Hook/Case/Knowledge/Template/MCP/ExpertAgent/Policy/确定性转换/ExecutionProvider 的资源创建与 Registry 登记流程",
        "status": SkillStatus.active,
        "skill_source": "rebuild_self",
        "directory_path": f"{_R_DIR}/R-资源创建登记/",
    },
    {
        "name": "R-工作准则",
        "series": SkillSeries.R,
        "category": SkillCategory.other,
        "description": "rebuild 建设核心工作准则：启动序列、任务路由、文档/产物生命周期、施工安全网、前端施工规则、安全与密钥规则、完成自检",
        "status": SkillStatus.active,
        "skill_source": "rebuild_self",
        "directory_path": f"{_R_DIR}/rebuild-work-guidelines/",
    },

    # ── P 系列（平台运行期，24 个，均有真实 SKILL.md，来源 ECC(MIT) 改写为信创迁移语境）──
    # P0 接入
    {"name": "P-codebase-onboarding", "series": SkillSeries.P, "category": SkillCategory.p0, "description": "P0 接入：引导用户接入遗留代码仓库、识别技术栈(.NET/Java/Oracle/MSSQL)、构建项目结构与依赖清单、诚实标注未知项——ECC codebase-onboarding(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p0/P-codebase-onboarding/"},
    # P1 建档
    {"name": "P-api-design", "series": SkillSeries.P, "category": SkillCategory.p1, "description": "P1 建档：分析遗留系统 API/接口契约并建档，作为迁移后目标 API 设计参考——ECC api-design(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p1/P-api-design/"},
    {"name": "full-stack-profiler", "series": SkillSeries.P, "category": SkillCategory.p1, "description": "P1 建档主工作流：全量项目识别基线（文件/目录/模块/语言/框架/构建/依赖/入口/测试/CI-CD/数据库/中间件/配置/文档），形成 P2-P6 可复用事实底座——rebuild 自有", "status": SkillStatus.platform_runtime, "skill_source": "rebuild", "directory_path": f"{_S_DIR}/p1/full-stack-profiler/"},
    {"name": "P-documentation-lookup", "series": SkillSeries.P, "category": SkillCategory.p1, "description": "P1 建档：经受控检索查达梦/openGauss/GaussDB/国产中间件官方迁移指南与兼容性矩阵，不臆测 API——ECC documentation-lookup(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p1/P-documentation-lookup/"},
    # P2 评估
    {"name": "P-architecture-decision-records", "series": SkillSeries.P, "category": SkillCategory.p2, "description": "P2 评估：将目标栈选型(达梦 vs openGauss、中间件替换)记录为 Nygard 式 ADR，作为评审与审计依据——ECC architecture-decision-records(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p2/P-architecture-decision-records/"},
    {"name": "P-migration-assessment", "series": SkillSeries.P, "category": SkillCategory.p2, "description": "P2 评估工作流：基于 P0/P1 阶段完成包与原始验收基准，评估 8 维度(兼容承载/现代化/DB迁移/部署中间件/PoC范围/阻塞验证缺口/资源/待确认)、目标库证据化对比+ADR(不选定单一库)、evidence_gap 诚实不预判终局——rebuild R17.5(D-108)", "status": SkillStatus.platform_runtime, "skill_source": "rebuild", "directory_path": f"{_S_DIR}/p2/P-migration-assessment/"},
    {"name": "P-blueprint", "series": SkillSeries.P, "category": SkillCategory.p2, "description": "P2 评估：把迁移目标转为可执行迁移蓝图/技术路线图/风险矩阵/依赖图，每步自包含冷启动 brief——ECC blueprint(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p2/P-blueprint/"},
    # P3 规划
    {"name": "P-migration-planning", "series": SkillSeries.P, "category": SkillCategory.p3, "description": "P3 规划主工作流：基于 P2 评估与用户 Gate 裁决，把迁移/重构转化为可执行/可授权/可验证/可追踪的 Stage Plan + Task Plan Batch + TaskGraph；条件化于用户裁决(未裁决=草案)、PoC 先行、每高风险任务必备回退、不臆造 P5 slot、不发明 artifact——rebuild R17.5(D-108)", "status": SkillStatus.platform_runtime, "skill_source": "rebuild", "directory_path": f"{_S_DIR}/p3/P-migration-planning/"},
    {"name": "P-agentic-engineering", "series": SkillSeries.P, "category": SkillCategory.p3, "description": "P3 规划：把迁移任务分解为可验证小单元、设计 TaskGraph、按复杂度路由模型、每单元定义验收——ECC agentic-engineering(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p3/P-agentic-engineering/"},
    {"name": "P-dynamic-workflow-mode", "series": SkillSeries.P, "category": SkillCategory.p3, "description": "P3 规划：按项目特征动态选择执行模式(Manual/Plan/Auto)，plan/queue/run/gate/handoff 对齐平台 run-state 与 HITL——ECC dynamic-workflow-mode(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p3/P-dynamic-workflow-mode/"},
    # P4 执行
    {"name": "P-migration-execution", "series": SkillSeries.P, "category": SkillCategory.p4, "description": "P4 执行主工作流：按 P3 TaskGraph 逐节点执行真实迁移工作包，给源路径+产物路径→按需读真实源→迁移→写 output_code+patch，每节点独立验收挂 P4→P5 Gate；不照节点标题臆造(禁通用 EMPLOYEE/无关 Vue3/Java)、无源绑定/无 Key/无环境诚实 blocked 或 evidence_gap、PoC vs Production 分级、对齐真实方言/框架转换、只产固定产物——rebuild R17.5(D-108)", "status": SkillStatus.platform_runtime, "skill_source": "rebuild", "directory_path": f"{_S_DIR}/p4/P-migration-execution/"},
    {"name": "P-database-migrations", "series": SkillSeries.P, "category": SkillCategory.p4, "description": "P4 执行：信创数据库迁移(Oracle→达梦/GaussDB、MSSQL→openGauss)——schema/对象/数据迁移、expand-contract、分批与三重校验、可回滚——ECC database-migrations(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p4/P-database-migrations/"},
    {"name": "P-dotnet-patterns", "series": SkillSeries.P, "category": SkillCategory.p4, "description": "P4 执行：.NET Framework(WebForms/WCF/MVC5)→.NET Core/ASP.NET Core 迁移模式，麒麟 Linux 运行兼容——ECC dotnet-patterns(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p4/P-dotnet-patterns/"},
    {"name": "P-backend-patterns", "series": SkillSeries.P, "category": SkillCategory.p4, "description": "P4 执行：迁移后服务分层架构(repository/service/router)、查询优化(国产DB执行计划差异)、缓存与连接池——ECC backend-patterns(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p4/P-backend-patterns/"},
    {"name": "P-error-handling", "series": SkillSeries.P, "category": SkillCategory.p4, "description": "P4 执行：迁移运行期类型化异常体系、指数退避重试、熔断、对外友好对内可诊断、不吞异常——ECC error-handling(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p4/P-error-handling/"},
    {"name": "P-docker-patterns", "series": SkillSeries.P, "category": SkillCategory.p4, "description": "P4 执行：迁移目标容器化到国产平台，麒麟/统信基础镜像、国产DB驱动层、非root安全、容器兼容性验证——ECC docker-patterns(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p4/P-docker-patterns/"},
    {"name": "P-fastapi-patterns", "series": SkillSeries.P, "category": SkillCategory.p4, "description": "P4 执行：当迁移目标为 Python/FastAPI（或平台自身后端参考）时的结构/事务/测试规范（多数信创目标为 .NET/Java 时仅参考）——ECC fastapi-patterns(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p4/P-fastapi-patterns/"},
    # P5 验证
    {"name": "P-benchmark", "series": SkillSeries.P, "category": SkillCategory.p5, "description": "P5 验证：迁移前建源系统性能基线→迁移后在信创栈复测→对比(达梦 vs GaussDB)、回归报告（工程性能基准）——ECC benchmark(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p5/P-benchmark/"},
    {"name": "P-eval-harness", "series": SkillSeries.P, "category": SkillCategory.p5, "description": "P5 验证：迁移正确性评估框架——迁移前定义断言(功能等价/数据一致/接口契约)、批量执行、pass@k + go/no-go——ECC eval-harness(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p5/P-eval-harness/"},
    {"name": "P-browser-qa", "series": SkillSeries.P, "category": SkillCategory.p5, "description": "P5 验证：迁移的 Web 应用(WebForms→现代前端)迁移前后浏览器级回归、截图对比、关键交互验证——ECC browser-qa(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p5/P-browser-qa/"},
    {"name": "P-code-tour", "series": SkillSeries.P, "category": SkillCategory.common, "description": "跨阶段(P1建档/P6交付)：生成锚定真实文件行的代码导览(SMIG)，帮助理解遗留系统与交付迁移变更说明——ECC code-tour(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p5/P-code-tour/"},
    # P6 交付
    {"name": "P-deployment-patterns", "series": SkillSeries.P, "category": SkillCategory.p6, "description": "P6 交付：信创环境(麒麟 on-prem、国产中间件、内网/离线)部署方案、回滚策略、上线检查清单——ECC deployment-patterns(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p6/P-deployment-patterns/"},
    {"name": "P-canary-watch", "series": SkillSeries.P, "category": SkillCategory.p6, "description": "P6 交付：迁移系统上线后灰度观测——关键接口健康、错误率、性能回归、回滚触发条件——ECC canary-watch(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/p6/P-canary-watch/"},
    # 跨阶段通用
    {"name": "P-council", "series": SkillSeries.P, "category": SkillCategory.common, "description": "跨阶段(P2-P5)：争议性目标栈选型/高风险决策时多视角(安全/性能/兼容性/可维护性)独立评审→汇总分歧→建议(不替代用户裁决)——ECC council(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/common/P-council/"},
    {"name": "P-context-budget", "series": SkillSeries.P, "category": SkillCategory.common, "description": "贯穿 P0-P6：控制每个 Agent 上下文预算、按需加载遗留代码/文档、防 token 超限，反馈 ModelGateway 用量——ECC context-budget(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/common/P-context-budget/"},
    {"name": "P-coding-standards", "series": SkillSeries.P, "category": SkillCategory.common, "description": "贯穿 P0-P6：迁移后生成代码的编码规范基线，按目标栈(C#/.NET、Java、国产框架)落地，与 P-dotnet/backend-patterns 配合——ECC coding-standards(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/common/P-coding-standards/"},
    {"name": "P-cost-aware-llm-pipeline", "series": SkillSeries.P, "category": SkillCategory.common, "description": "贯穿 P0-P6：按任务复杂度路由模型(便宜模型机械转换/强模型复杂判断)、预算追踪、仅瞬时错误重试、缓存，经 ModelGateway 统一计量——ECC cost-aware-llm-pipeline(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/common/P-cost-aware-llm-pipeline/"},
    {"name": "P-automation-audit-ops", "series": SkillSeries.P, "category": SkillCategory.common, "description": "跨阶段(P5-P6)：对迁移项目自动化/脚本/任务做证据优先盘点(清查遗留定时任务/批处理/集成)、分类、生成审计 Trail——ECC automation-audit-ops(MIT)", "status": SkillStatus.platform_runtime, "skill_source": "ecc", "directory_path": f"{_S_DIR}/common/P-automation-audit-ops/"},
]


RESOURCE_SEEDS = [
    # Agent entries
    {"resource_type": ResourceType.agent, "name": "Node Worker Agent Registry Entry", "status": ResourceStatus.active, "risk_level": RiskLevel.L2, "source_type": SourceType.internal_current, "source_trust_level": TrustLevel.trusted_current, "permission_scope": "read_workspace"},
    {"resource_type": ResourceType.agent, "name": "Acceptance Agent Registry Entry", "status": ResourceStatus.active, "risk_level": RiskLevel.L1, "source_type": SourceType.internal_current, "source_trust_level": TrustLevel.trusted_current, "permission_scope": "read_only"},

    # Skill entries
    {"resource_type": ResourceType.skill, "name": "R 系列建设 Skill（3 个）", "status": ResourceStatus.active, "risk_level": RiskLevel.L1, "source_type": SourceType.internal_current, "source_trust_level": TrustLevel.trusted_current, "description": "R-数据库施工规范 / R-资源创建登记 / R-工作准则，均有真实 SKILL.md（skills/R-建设执行/，置于 source/ 之外与平台运行期隔离）"},
    {"resource_type": ResourceType.skill, "name": "P 系列平台 Skill（28 个）", "status": ResourceStatus.active, "risk_level": RiskLevel.L1, "source_type": SourceType.community, "source_trust_level": TrustLevel.reviewed_reference, "source_path_or_ref": f"{_S_DIR}/", "description": "24 个源自 ECC(MIT) 改写为信创迁移语境 + 4 个 rebuild 自有 P 阶段主工作流 skill（full-stack-profiler P1 建档 / P-migration-assessment P2 评估 / P-migration-planning P3 规划 / P-migration-execution P4 执行，均 D-108），覆盖 P0-P6 + 跨阶段，均有真实 SKILL.md；原 30 候选剔除 6 个非迁移类（市场对标/营销/UI美学/harness运维/工具选型/运维监控）", "type_metadata": {"count": 28, "source_repo": "https://github.com/affaan-m/ECC", "license": "MIT", "dropped_non_migration": ["benchmark-methodology", "content-engine", "design-system", "config-gc", "agent-eval", "dashboard-builder"]}},

    # Tool entries — B-TOOL-SCHEMA-1 (R11-3): every tool carries a real OpenAI
    # `parameters` schema so the agent can call it WITH arguments (empty schema
    # meant the LLM saw the tool but had no arg to fill). Param names match what
    # tool_registry.execute_tool / _execute_* actually read.
    {"resource_type": ResourceType.tool, "name": "fs_read", "description": "读取 workspace 内某个文件的内容（只读，限 workspace 内）", "status": ResourceStatus.active, "risk_level": RiskLevel.L1, "type_metadata": {"dry_run_supported": True, "write_scope": "none", "binds_via": "execution_provider(R8)", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "要读取的文件路径，相对 workspace 根，如 source/xxx.cs 或 artifacts/p0/intake_report.json"}}, "required": ["path"]}}},
    {"resource_type": ResourceType.tool, "name": "list_files", "description": "列举 workspace 某个目录下的文件与子目录（只读）", "status": ResourceStatus.active, "risk_level": RiskLevel.L1, "type_metadata": {"dry_run_supported": True, "write_scope": "none", "binds_via": "execution_provider(R8)", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "要列举的目录路径，相对 workspace 根，默认 source/"}}, "required": []}}},
    {"resource_type": ResourceType.tool, "name": "code_grep", "description": "在 workspace 代码库中按文本/正则模式检索（只读）", "status": ResourceStatus.active, "risk_level": RiskLevel.L1, "type_metadata": {"dry_run_supported": True, "write_scope": "none", "binds_via": "execution_provider(R8)", "parameters": {"type": "object", "properties": {"pattern": {"type": "string", "description": "要检索的文本或正则模式"}, "path": {"type": "string", "description": "检索范围目录，相对 workspace 根，默认 source/"}}, "required": ["pattern"]}}},
    {"resource_type": ResourceType.tool, "name": "fs_write_artifact", "description": "写出 Artifact 到 workspace（受控，经 WorkspaceMediator）", "status": ResourceStatus.active, "risk_level": RiskLevel.L2, "type_metadata": {"dry_run_supported": True, "write_scope": "workspace", "binds_via": "execution_provider(R8)", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "产物写入路径，相对 workspace，须在 artifacts/ 或 output_code/ 下"}, "content": {"type": "string", "description": "要写入的文件内容"}}, "required": ["path", "content"]}}},
    {"resource_type": ResourceType.tool, "name": "generate_patch", "description": "生成补丁草案写入 patches/（不直接落盘 source/，待确认）", "status": ResourceStatus.active, "risk_level": RiskLevel.L2, "type_metadata": {"dry_run_supported": True, "write_scope": "patch_draft", "binds_via": "execution_provider(R8)", "parameters": {"type": "object", "properties": {"target_path": {"type": "string", "description": "补丁针对的目标文件路径（相对 workspace）"}, "diff": {"type": "string", "description": "unified diff 文本，或期望的新内容"}}, "required": ["target_path", "diff"]}}},
    {"resource_type": ResourceType.tool, "name": "apply_patch_with_confirm", "description": "应用补丁到产出代码 output_code/（需用户 Gate；源码 source/ 始终只读，D-099）", "status": ResourceStatus.active, "risk_level": RiskLevel.L4, "permission_scope": "write_with_gate", "type_metadata": {"dry_run_supported": True, "write_scope": "output_code", "requires_gate": True, "binds_via": "execution_provider(R8)", "parameters": {"type": "object", "properties": {"patch_ref": {"type": "string", "description": "patches/ 下待应用的补丁草案引用路径"}, "target_path": {"type": "string", "description": "应用到 output_code/ 下的目标路径"}}, "required": ["patch_ref"]}}},
    {"resource_type": ResourceType.tool, "name": "run_safe_command", "description": "运行白名单内安全命令并采集 stdout/exit_code（测试白名单+审计）", "status": ResourceStatus.active, "risk_level": RiskLevel.L4, "permission_scope": "exec_with_gate", "type_metadata": {"dry_run_supported": False, "requires_gate": True, "binds_via": "execution_provider(R8)", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "要执行的命令（须在白名单内），经 ExecutionProvider 隔离执行"}}, "required": ["command"]}}},

    # Hook entries
    {"resource_type": ResourceType.hook, "name": "pre-write Policy check", "description": "任何文件写操作前的 Policy 校验（PreToolUse，block 模式）", "status": ResourceStatus.active, "risk_level": RiskLevel.L2, "type_metadata": {"hook_point": "PreToolUse", "hook_mode": "block", "hook_impl": "pre_write_policy"}},
    {"resource_type": ResourceType.hook, "name": "pre-commit quality check", "description": "提交前 lint/secret/console.log 扫描（ECC 分级 Hook 参考，warn 模式）", "status": ResourceStatus.planned, "risk_level": RiskLevel.L1, "type_metadata": {"hook_point": "PreToolUse", "hook_mode": "warn", "ref": "ECC hooks(MIT)"}, "source_type": SourceType.community, "source_trust_level": TrustLevel.read_only_reference},

    # Case entries (always read_only, never executable)
    {"resource_type": ResourceType.case, "name": "信创迁移候选案例", "description": "参考案例——来源旧版 WebForms→Vue3 迁移", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "source_type": SourceType.internal_archive, "source_trust_level": TrustLevel.read_only_reference, "permission_scope": "read_only", "type_metadata": {"executable": False, "never_execute": True}},
    {"resource_type": ResourceType.case, "name": "WebForms 重写规则（已审核）", "description": "确定性转换规则案例——已审核，不可直接执行", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "source_type": SourceType.internal_archive, "source_trust_level": TrustLevel.read_only_reference, "permission_scope": "read_only", "type_metadata": {"executable": False, "never_execute": True}},

    # Knowledge entries (T5.5/R9-5-4: source_type=internal_current so platform docs tab shows correctly)
    {"resource_type": ResourceType.knowledge, "name": "ECC Security Guide 11 基线", "description": "ECC the-security-guide.md——11 条 Agent 安全最低基线（隔离/最小权限/审批边界/可观测/Kill Switch）", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "source_type": SourceType.internal_current, "source_trust_level": TrustLevel.read_only_reference, "source_path_or_ref": "https://github.com/affaan-m/ECC/blob/main/the-security-guide.md"},
    {"resource_type": ResourceType.knowledge, "name": "ECC Agent 结构模板参考", "description": "ECC agents/——Prompt Defense Baseline + Pre-Report Gate + Approval Criteria", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "source_type": SourceType.internal_current, "source_trust_level": TrustLevel.read_only_reference, "source_path_or_ref": "https://github.com/affaan-m/ECC/tree/main/agents"},
    {"resource_type": ResourceType.knowledge, "name": "ECC AGENTS.md 结构约定参考", "description": "ECC AGENTS.md——agent-first/TDD/security-first/immutability/plan-before-execute + Workflow Surface Policy(skills 优先于 commands)", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "source_type": SourceType.internal_current, "source_trust_level": TrustLevel.read_only_reference, "source_path_or_ref": "https://github.com/affaan-m/ECC/blob/main/AGENTS.md"},

    # Template entries (always "not fact source")
    {"resource_type": ResourceType.template, "name": "Agent Definition Contract 模板", "description": "13 要素契约模板——不是事实源", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "type_metadata": {"not_fact_source": True}},
    {"resource_type": ResourceType.template, "name": "Resource Registry 条目模板", "description": "12 类资源登记字段模板——不是事实源", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "type_metadata": {"not_fact_source": True}},
    {"resource_type": ResourceType.template, "name": "P 系列 SKILL.md 模板", "description": "P 系列 Skill 标准结构（触发/输入/步骤/产物/质量门/信创要点/反例/集成/参考）——不是事实源", "status": ResourceStatus.read_only, "risk_level": RiskLevel.L0, "type_metadata": {"not_fact_source": True}},

    # MCP entries
    {"resource_type": ResourceType.mcp, "name": "MCP Server 占位", "description": "MCP Server 接口预留——R8 施工", "status": ResourceStatus.not_connected, "risk_level": RiskLevel.L0, "source_type": SourceType.internal_current, "source_trust_level": TrustLevel.unreviewed, "type_metadata": {"available": False, "write_enabled": False}},

    # Expert Agent entries
    {"resource_type": ResourceType.expert_agent, "name": "外部编程 Agent 参考", "description": "OpenCode/Claude Code/Cursor Agent 等外部编程 Agent——非最终裁决主体", "status": ResourceStatus.not_connected, "risk_level": RiskLevel.L0, "source_type": SourceType.community, "source_trust_level": TrustLevel.read_only_reference, "type_metadata": {"not_final_arbiter": True}},

    # Policy entries
    {"resource_type": ResourceType.policy, "name": "AGENTS.md §18 禁止事项总表", "description": "通用禁止与红线完整总表——优先级高于 Agent 判断", "status": ResourceStatus.active, "risk_level": RiskLevel.L5, "type_metadata": {"policy_type": "hard_constraint"}},
    {"resource_type": ResourceType.policy, "name": "BYOK 零泄露策略", "description": "Key 不落盘明文/不进日志/不进 Trace/不进 Audit/不进 API 响应/不进截图/不进报告", "status": ResourceStatus.active, "risk_level": RiskLevel.L5, "type_metadata": {"policy_type": "hard_constraint"}},

    # Deterministic Transformer entries
    {"resource_type": ResourceType.deterministic_transformer, "name": "AST Transform 占位", "description": "AST/codemod 确定性转换——R11 P4 执行阶段施工", "status": ResourceStatus.planned, "risk_level": RiskLevel.L2, "type_metadata": {"converter_type": "ast", "dry_run_supported": True, "rollback_supported": False, "write_scope": "patch_draft"}},

    # Execution Provider entries
    {"resource_type": ResourceType.execution_provider, "name": "Local Execution Provider", "description": "本地执行器——R8 Workspace/Session 施工", "status": ResourceStatus.not_connected, "risk_level": RiskLevel.L0, "type_metadata": {"provider_type": "local", "capabilities": ["list_files", "read_file", "grep", "write_artifact", "generate_patch", "apply_patch_with_confirm", "run_safe_command", "collect_stdout_exit_code"]}},

    # Local existing CC skills (not duplicated as R-series)
    {"resource_type": ResourceType.skill, "name": "skill-creator (CC 本地已存在)", "description": "Claude Code skill-creator——本地已安装，不创建 R 系列副本", "status": ResourceStatus.local_existing, "risk_level": RiskLevel.L0, "source_type": SourceType.external_online, "source_trust_level": TrustLevel.read_only_reference, "type_metadata": {"license": "Apache 2.0", "local_path": "~/.claude/skills/skill-creator/"}},
    {"resource_type": ResourceType.skill, "name": "webapp-testing (CC 本地已存在)", "description": "Claude Code webapp-testing——本地已安装，不创建 R 系列副本", "status": ResourceStatus.local_existing, "risk_level": RiskLevel.L0, "source_type": SourceType.external_online, "source_trust_level": TrustLevel.read_only_reference, "type_metadata": {"license": "Apache 2.0", "local_path": "~/.claude/skills/webapp-testing/"}},
    {"resource_type": ResourceType.skill, "name": "frontend-design (CC 本地已存在)", "description": "Claude Code frontend-design——本地已安装，不创建 R 系列副本", "status": ResourceStatus.local_existing, "risk_level": RiskLevel.L0, "source_type": SourceType.external_online, "source_trust_level": TrustLevel.read_only_reference, "type_metadata": {"license": "Apache 2.0", "local_path": "~/.claude/skills/frontend-design/"}},
]


def seed_all(db: Session) -> dict:
    """Seed initial data. Returns counts seeded."""
    counts = {"agents": 0, "skills": 0, "resources": 0, "coding_agents": 0}

    # Agents
    existing = db.query(AgentDefinition).count()
    if existing == 0:
        for s in AGENT_SEEDS:
            db.add(AgentDefinition(**s))
        counts["agents"] = len(AGENT_SEEDS)

    # Skills — 增量按 name 补种缺失项（R17.5 P2：修 seed_all 幂等"非空即全跳过"隐患——
    # 既有非空 DB 下新增的 skill（如 P-migration-assessment）此前永不入库，致 context_assembler
    # 装配不到、skill-first 名存实亡。改为按 name 去重补种缺失 skill，不重复插入已存在项）。
    # R17.5 P2 返工3：先按 name 去重清理历史遗留重复行（真实 DB 每 skill 曾 2×，致装配翻倍、
    # 主 skill 被挤出 skill_body 预算，Q-R17.5-P2-6）——保留每 name 首行、删除其余；防复发。
    seen_names: set[str] = set()
    removed_dupes = 0
    for row in db.query(SkillDefinition).order_by(SkillDefinition.name, SkillDefinition.skill_id).all():
        if row.name in seen_names:
            db.delete(row)
            removed_dupes += 1
        else:
            seen_names.add(row.name)
    if removed_dupes:
        db.flush()
    counts["skills_deduped"] = removed_dupes
    existing_skill_names = seen_names
    added_skills = 0
    for s in SKILL_SEEDS:
        if s["name"] not in existing_skill_names:
            db.add(SkillDefinition(**s))
            added_skills += 1
    counts["skills"] = added_skills

    # Resources — R17.5-P4-FIX 批1：先按 name 清理历史遗留重复 tool 记录（防复发）。
    # 真实 DB 中 fs_write_artifact/generate_patch/apply_patch_with_confirm/run_safe_command
    # 各 3 份：R6 原始 seed 1 份（含完整 parameters schema + binds_via + 正确权限位），
    # 另有 R17.2 开发期一次性注册路径（现已不存在）又插入 2 份退化副本（type_metadata 仅
    # {tool_name,requires_gate}、无 parameters、权限位退化为 read_only）。重复函数名致
    # load_schemas 吐重名 tool → OpenAI 兼容端点 bad_request → P4 工具循环恒崩→零迁移产物。
    # seed_all 的资源初始插入本就以 existing==0 守门（不重复插入），此处再加去重 pass 主动
    # 清理任意来源产生的重名 tool（自愈+幂等）：每 name 保留 type_metadata 含 parameters 的
    # 富记录，删除退化副本；无引用（已核 agent/skill required_tools 与全表无外键/JSON 引用）。
    tool_rows = db.query(ResourceEntry).filter(ResourceEntry.resource_type == ResourceType.tool).all()
    by_name: dict[str, list] = {}
    for r in tool_rows:
        by_name.setdefault(r.name, []).append(r)
    removed_tool_dupes = 0
    for _name, rows in by_name.items():
        if len(rows) <= 1:
            continue
        # 富度排序：优先含 parameters schema，其次 metadata 更完整，其次更早创建
        def _richness(r):
            meta = r.type_metadata or {}
            return (1 if meta.get("parameters") else 0, len(str(meta)), r.created_at is not None)
        rows_sorted = sorted(rows, key=_richness, reverse=True)
        for extra in rows_sorted[1:]:
            db.delete(extra)
            removed_tool_dupes += 1
    if removed_tool_dupes:
        db.flush()
    counts["tool_dupes_removed"] = removed_tool_dupes

    existing = db.query(ResourceEntry).count()
    if existing == 0:
        for s in RESOURCE_SEEDS:
            db.add(ResourceEntry(**s))
        counts["resources"] = len(RESOURCE_SEEDS)

    # Coding Agent: pre-seed one OpenCode config if none exist
    existing_ca = db.query(CodingAgentConfig).count()
    if existing_ca == 0:
        import shutil
        oc_available = shutil.which("opencode") is not None
        db.add(CodingAgentConfig(
            agent_type=CodingAgentType.opencode_cli,
            name="OpenCode (本地 CLI)",
            invoke_mode=CodingAgentInvokeMode.cli,
            config={
                # D-098：不预置 model/base_url——运行时经
                # resolve_model_defaults → OpenCodeModelResolver → ModelGateway
                # 按用户策略动态解析，避免硬编码 endpoint 绕过网关（单一事实源）。
                "timeout_seconds": 180,
                "note": "model/base_url 由平台 ModelGateway 策略动态解析；API Key 由 LLM_API_KEY 环境变量注入，均不存储于此处",
            },
            status=CodingAgentStatus.connected if oc_available else CodingAgentStatus.not_configured,
            enabled=True,
        ))
        counts["coding_agents"] = 1

    db.commit()
    return counts


# ── R15-4-C8: ModelCatalog seeder (from model_profiles.yaml) ────────────────

def seed_model_catalog(db: Session) -> int:
    """Seed ModelCatalogEntry from config/model_profiles.yaml (idempotent).

    Only seeds when the catalog table is empty, so it never overwrites operator
    edits. Coexists with the runtime ModelProfile/ModelGateway in-memory structs.
    """
    from datetime import datetime, timezone
    from app.models.model_catalog import ModelCatalogEntry
    if db.query(ModelCatalogEntry).count() > 0:
        return 0

    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent / "config" / "model_profiles.yaml"
    if not cfg_path.exists():
        return 0
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    now = datetime.now(timezone.utc)
    n = 0
    for prov in data.get("providers", []):
        prov_id = prov.get("provider_id", "")
        for m in prov.get("models", []):
            model_id = m.get("model_name", "")
            if not model_id:
                continue
            catalog_id = f"{prov_id}/{model_id}"
            context = m.get("context_window")
            # allow numeric context_window; yaml note is a string like "1M tokens"
            if isinstance(context, str):
                context = None
            db.add(ModelCatalogEntry(
                catalog_id=catalog_id,
                model_id=model_id,
                provider_id=prov_id,
                display_name=m.get("display_name", model_id),
                family=model_id.split("-")[0] if "-" in model_id else model_id,
                model_version=model_id,
                context_window=context,
                capability_tags=m.get("capability_tags") or [],
                task_tags=[],
                availability_status="available",
                official_url=None,
                official_icon_url=None,
                license=None,
                source="seed",
                updated_at=now,
            ))
            n += 1
    db.commit()
    return n
