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

    # CORS
    cors_origins: str = "http://localhost:5173"

    # Debug
    debug: bool = True

    # Source resources base directory (skills, agents, mcp, resources, cases, knowledge)
    source_dir: str = "/home/king/rebuild/source"

    # R7 GitHub OAuth
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8765/api/integrations/git/oauth/github/callback"
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


# Module-level singleton for convenience (used by database, alembic, etc.)
settings = Settings()
