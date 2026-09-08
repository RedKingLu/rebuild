"""Application configuration via pydantic-settings.

Loads from .env file and environment variables.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="REBUILD_",
        extra="ignore",
        frozen=True,
    )

    # Data
    data_dir: str = "./.data"
    database_url: str = "sqlite:////home/king/rebuild/backend/.data/rebuild.db"

    # CORS — allow BOTH dev (Vite 5173) and container (nginx 8080) frontends.
    # 端口标准单一事实源：文档/02-架构设计/06-容器化部署与执行隔离规范.md §1.1
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # Debug
    debug: bool = True

    # Source resources base directory (skills, agents, mcp, resources, cases, knowledge)
    source_dir: str = "/home/king/rebuild/source"

    # Workspace root — per-project isolated directories (D-050)
    workspace_dir: str = "/home/king/rebuild/工作区"

    # R19-3-05 硬编码收敛：对外声明的平台版本号单一来源（MCP clientInfo 等协议握手复用）。
    # main.py / routes_health.py 的版本号属 FastAPI/health 合理声明，按验收口径不动。
    app_version: str = "V26.1.1"

    # R19-3-05 硬编码收敛：P6 出网能力探测目标（原硬编码 1.1.1.1:53）。
    # 内网/离线部署可经 env 改指内部可达地址；仅做 TCP 连通探测，不携带凭据。
    network_probe_host: str = "1.1.1.1"
    network_probe_port: int = 53

    # R17-2 V-R17-1B-1/P1-4：当前建设阶段（单一事实源，供 /api/health 与 /api/version 输出）。
    # 随 R 阶段推进手动更新；未知时返 "unknown"（不硬编码过期值）。
    r_stage: str = "R17"

    # R15-4 Community Connector：独立社区服务 base URL（可切换本地/未来远程官方社区）。
    # 端口标准：community-backend 8001（见 06-容器化部署与执行隔离规范 §1.1）。
    community_base_url: str = "http://localhost:8001"

    # R19-1 G1 工具链容器构建（toolchain_build 档）。**不新增任何服务端口**（AGENTS §10-19）。
    # toolchain_container_enabled：宿主无对应 SDK 时是否允许走工具链容器通道产真实构建事实。
    #   关掉 = 回到"诚实 needs_user_input"的旧行为（kill switch，便于排障与离线环境）。
    # toolchain_cache_dir：包缓存目录（跨轮复用）。**不复用宿主 ~/.nuget**（可能带私有源凭据）。
    # toolchain_build_timeout_s：单个构建阶段（restore / build）的超时上限。
    toolchain_container_enabled: bool = True
    toolchain_cache_dir: str = "/home/king/rebuild/backend/.data/toolchain-cache"
    toolchain_build_timeout_s: int = 900

    # R19-2 G2 依赖真实性校验（dependency_registry_service.py）。只读查询公共 registry
    # （nuget.org / npmjs.org / Maven Central），不新增服务端口，不新增第三方依赖。
    # dependency_check_enabled：kill switch（关闭 = 结论一律 not_checked(skipped_by_config)，
    #   P4 行为回到本轮施工前，运行期熔断不需要改代码，见方案 §3.18 回滚方案）。
    # dependency_check_cache_ttl_s：只缓存确定结论（resolvable/package_not_found/
    #   version_not_found）；indeterminate 一律不缓存（否则一次限流会被缓存并持续误报）。
    # dependency_check_timeout_s：单次 HTTP 请求超时。
    # dependency_check_budget_s：整轮依赖校验的墙钟总预算，超时未查完的坐标诚实
    #   evidence_gap(budget_exhausted)，不默认通过。
    # dependency_check_max_retries：429/5xx/超时类的最多重试次数（404 绝不重试）。
    dependency_check_enabled: bool = True
    dependency_check_cache_ttl_s: int = 900
    dependency_check_timeout_s: float = 8.0
    dependency_check_budget_s: float = 60.0
    dependency_check_max_retries: int = 2

    # R21 长时任务心跳监视（B-R20-NO-LONGTASK-MONITOR）。最小心跳注册表：只发现挂起并诚实
    # 标记，不做自动重试/自动接管（D-037 硬约束：LangGraph 仍是唯一编排）。不新建任务队列/DB
    # 表（D-065 NIH）；心跳落 workspace artifacts/ 文件，经现有 WorkspaceMediator 写盘中介
    # （D-099）。此阈值判定"心跳距今多久算 stalled"，可经 env 覆盖，不在各处散落魔法数字。
    p4_heartbeat_stall_threshold_s: float = 300.0

    # R7 GitHub OAuth
    # redirect_uri 必须与 GitHub OAuth App 注册的回调一致，且在 dev/容器两套拓扑下不变：
    # 后端统一监听 8000 → 注册一次即可两套通用（端口标准见 06-容器化部署与执行隔离规范 §1.1）。
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8000/api/integrations/git/oauth/github/callback"
    # 登录后落地的前端地址：唯一允许随拓扑变化的值（dev=5173 / 容器=8080），经 env 覆盖，不注册到 GitHub。
    frontend_url: str = "http://localhost:5173"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def source_path(self) -> Path:
        return Path(self.source_dir).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def community_base_url_normalized(self) -> str:
        return self.community_base_url.rstrip("/")


# Module-level singleton for convenience (used by database, alembic, etc.)
settings = Settings()
