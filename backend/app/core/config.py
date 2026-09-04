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
