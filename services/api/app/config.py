"""Typed application settings sourced from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets are supplied outside source control."""

    app_env: str = "development"
    web_origin: str = "http://localhost:5173"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://osa_app:local-only@localhost:5432/osa"
    redis_state_url: str = "redis://:local-only@localhost:6379/0"
    redis_cache_url: str = "redis://:local-only@localhost:6379/1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def docs_url(self) -> str | None:
        """Keep interactive API docs out of production deployments."""
        return "/docs" if self.app_env != "production" else None

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.web_origin.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
