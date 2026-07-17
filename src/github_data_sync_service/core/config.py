from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from github_data_sync_service import __version__


class Settings(BaseSettings):
    app_env: str = Field(default="local", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(
        default="postgresql+psycopg://github_data_sync:github_data_sync@localhost:5432/github_data_sync",
        alias="DATABASE_URL",
    )
    github_token: SecretStr | None = Field(default=None, alias="GITHUB_TOKEN")
    github_api_base_url: str = Field(default="https://api.github.com", alias="GITHUB_API_BASE_URL")
    github_api_version: str = Field(default="2022-11-28", alias="GITHUB_API_VERSION")
    github_user_agent: str = Field(
        default=f"github-data-sync-service/{__version__}", alias="GITHUB_USER_AGENT"
    )
    github_connect_timeout_seconds: float = Field(default=5, alias="GITHUB_CONNECT_TIMEOUT_SECONDS")
    github_read_timeout_seconds: float = Field(default=15, alias="GITHUB_READ_TIMEOUT_SECONDS")
    github_max_attempts: int = Field(default=3, ge=1, alias="GITHUB_MAX_ATTEMPTS")
    worker_poll_interval_seconds: float = Field(
        default=5, ge=0, alias="WORKER_POLL_INTERVAL_SECONDS"
    )
    worker_id: str = Field(default="worker-local", alias="WORKER_ID")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @property
    def github_token_value(self) -> str | None:
        if self.github_token is None:
            return None
        value = self.github_token.get_secret_value()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
