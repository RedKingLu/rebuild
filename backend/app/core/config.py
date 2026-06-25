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

    # CORS
    cors_origins: str = "http://localhost:5173"

    # Debug
    debug: bool = True

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
