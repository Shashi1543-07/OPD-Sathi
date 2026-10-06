"""Typed application settings sourced from environment variables."""

import base64
import binascii
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets are supplied outside source control."""

    app_env: str = "development"
    web_origin: str = "http://localhost:5173"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://osa_app:local-only@localhost:5432/osa"
    worker_database_url: str = ""
    migrations_database_url: str = "postgresql+asyncpg://osa_migrator:local-only@localhost:5432/osa"
    redis_state_url: str = "redis://:local-only@localhost:6379/0"
    redis_cache_url: str = "redis://:local-only@localhost:6379/1"
    jwt_secret: str = "development-only-signing-secret-change-before-deploy"
    jwt_kid: str = "k1"
    jwt_issuer: str = "opd-saathi"
    jwt_audience: str = "opd-saathi-api"
    cursor_hmac_key: str = "development-only-cursor-key-change-before-deploy"
    app_data_key: str = ""
    app_hmac_key: str = ""
    stt_provider: str = "mock"
    stt_model: str = "saaras:v3-realtime"
    sarvam_api_key: str = ""
    tts_provider: str = "mock"
    tts_model: str = "bulbul:v3"
    store_audio: bool = False
    notification_provider: str = "console"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    llm_provider: str = "mock"
    llm_model: str = "gemini-3.8-flash"
    llm_fallback_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: str = ""
    llm_enabled: bool = True
    llm_max_calls_per_session: int = 30
    llm_max_output_tokens: int = 1024
    access_token_minutes: int = 10
    refresh_token_hours: int = 8
    kiosk_token_hours: int = 12
    patient_session_hours: int = 2
    max_request_body_bytes: int = 1_048_576
    metrics_enabled: bool = False
    urgent_bonus_min: int = 360
    priority_bonus_min: int = 45
    vulnerable_bonus_min: int = 15
    default_consult_min: float = 4.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production")
        if self.stt_provider not in {"mock", "sarvam"}:
            raise ValueError("STT_PROVIDER must be mock or sarvam")
        if self.tts_provider not in {"mock", "sarvam", "google"}:
            raise ValueError("TTS_PROVIDER must be mock, sarvam, or google")
        if self.llm_provider not in {"mock", "gemini"}:
            raise ValueError("LLM_PROVIDER must be mock or gemini")
        if self.notification_provider not in {"console", "twilio", "whatsapp"}:
            raise ValueError("NOTIFICATION_PROVIDER must be console, twilio, or whatsapp")
        if self.notification_provider == "twilio" and not all(
            value.strip()
            for value in (self.twilio_account_sid, self.twilio_auth_token, self.twilio_from_number)
        ):
            raise ValueError("Twilio credentials and sender are required when enabled")
        if self.notification_provider == "whatsapp" and not all(
            value.strip() for value in (self.whatsapp_access_token, self.whatsapp_phone_number_id)
        ):
            raise ValueError("WhatsApp credentials are required when enabled")
        if not 1 <= self.llm_max_calls_per_session <= 30:
            raise ValueError("LLM_MAX_CALLS_PER_SESSION must be between 1 and 30")
        if not 1 <= self.llm_max_output_tokens <= 1024:
            raise ValueError("LLM_MAX_OUTPUT_TOKENS must be between 1 and 1024")
        if self.llm_provider == "gemini" and not self.gemini_api_key.strip():
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        if self.tts_provider == "sarvam" and not self.sarvam_api_key.strip():
            raise ValueError("SARVAM_API_KEY is required when TTS_PROVIDER=sarvam")
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
        if self.app_env == "production":
            for setting_name, encoded in (
                ("APP_DATA_KEY", self.app_data_key),
                ("APP_HMAC_KEY", self.app_hmac_key),
            ):
                try:
                    key = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(
                        f"production {setting_name} must be base64-encoded 32-byte key material"
                    ) from exc
                if len(key) != 32:
                    raise ValueError(f"production {setting_name} must decode to exactly 32 bytes")
        if not self.jwt_kid or len(self.jwt_kid) > 64:
            raise ValueError("JWT_KID must contain between 1 and 64 characters")
        if self.max_request_body_bytes < 1024:
            raise ValueError("MAX_REQUEST_BODY_BYTES must be at least 1024")
        if min(self.urgent_bonus_min, self.priority_bonus_min, self.vulnerable_bonus_min) < 0:
            raise ValueError("queue score bonuses cannot be negative")
        if not 1 <= self.default_consult_min <= 15:
            raise ValueError("DEFAULT_CONSULT_MIN must be between 1 and 15")
        return self

    @property
    def docs_url(self) -> str | None:
        """Keep interactive API docs out of production deployments."""
        return "/docs" if self.app_env != "production" else None

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.web_origin.split(",") if origin.strip()]

    @property
    def app_data_key_bytes(self) -> bytes:
        return _decode_key(self.app_data_key, "APP_DATA_KEY")

    @property
    def app_hmac_key_bytes(self) -> bytes:
        return _decode_key(self.app_hmac_key, "APP_HMAC_KEY")


def _decode_key(encoded: str, name: str) -> bytes:
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} must be base64-encoded 32-byte key material") from exc
    if len(key) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes")
    return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
