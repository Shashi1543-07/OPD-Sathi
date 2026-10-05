"""Typed application settings sourced from environment variables."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets are supplied outside source control."""

    app_env: str = "development"
    web_origin: str = "http://localhost:5173"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://osa_app:local-only@localhost:5432/osa"
    migrations_database_url: str = "postgresql+asyncpg://osa_migrator:local-only@localhost:5432/osa"
    redis_state_url: str = "redis://:local-only@localhost:6379/0"
    redis_cache_url: str = "redis://:local-only@localhost:6379/1"
    jwt_secret: str = "development-only-signing-secret-change-before-deploy"
    jwt_kid: str = "k1"
    jwt_issuer: str = "opd-saathi"
    jwt_audience: str = "opd-saathi-api"
    cursor_hmac_key: str = "development-only-cursor-key-change-before-deploy"
    access_token_minutes: int = 10
    refresh_token_hours: int = 8
    kiosk_token_hours: int = 12
    patient_session_hours: int = 2
    max_request_body_bytes: int = 1_048_576

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production")
        if self.access_token_minutes != 10:
            raise ValueError("staff access tokens must expire after 10 minutes")
        if min(self.refresh_token_hours, self.kiosk_token_hours, self.patient_session_hours) < 1:
            raise ValueError("refresh, kiosk, and patient token lifetimes must be positive")
        if not self.jwt_issuer.strip() or not self.jwt_audience.strip():
            raise ValueError("JWT_ISSUER and JWT_AUDIENCE must not be empty")
        if self.app_env == "production" and (
            len(self.jwt_secret.encode("utf-8")) < 32
            or self.jwt_secret.startswith("development-only-")
        ):
            raise ValueError("production JWT_SECRET must contain at least 32 random bytes")
        if self.app_env == "production" and len(self.cursor_hmac_key.encode("utf-8")) < 32:
            raise ValueError("production CURSOR_HMAC_KEY must contain at least 32 random bytes")
        if not self.jwt_kid or len(self.jwt_kid) > 64:
            raise ValueError("JWT_KID must contain between 1 and 64 characters")
        if self.max_request_body_bytes < 1024:
            raise ValueError("MAX_REQUEST_BODY_BYTES must be at least 1024")
        return self

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
