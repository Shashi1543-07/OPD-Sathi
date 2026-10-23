"""Typed application settings sourced from environment variables."""

import base64
import binascii
import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

if TYPE_CHECKING:
    from app.core.crypto import KeyRing

STAFF_ROLES = ("admin", "doctor", "nurse")


class _FileReferenceSource(PydanticBaseSettingsSource):
    """``<NAME>_FILE=/run/secrets/name``: read a setting from a file (Compose secrets).

    Production mounts every secret as a file so none appears in the container's environment,
    ``docker inspect`` or the process list (Phase 7, task 7.3.7). A file reference wins over
    the plain variable of the same name.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name in self.settings_cls.model_fields:
            reference = os.environ.get(f"{name.upper()}_FILE", "")
            if not reference:
                continue
            try:
                values[name] = Path(reference).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(f"{name.upper()}_FILE points to an unreadable file") from exc
        return values


class Settings(BaseSettings):
    """Runtime configuration; secrets are supplied outside source control."""

    app_env: str = "development"
    # Which process reads these settings: the API holds the patient-data keys, the worker
    # must not (docker-compose.yml sets PROCESS_ROLE=worker for it).
    process_role: str = "api"
    provider_mode: str = "standard"
    demo_story: str = "hindi-fever"
    web_origin: str = "http://localhost:5173"
    # Address patients' phones use to open the token QR link, e.g. http://192.168.1.20:5173.
    # The kiosk itself runs on localhost (needed for the microphone), which a phone cannot
    # reach. Empty: the QR uses the kiosk's own address.
    public_web_url: str = ""
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://osa_app:local-only@localhost:5432/osa"
    # Optional read-only replica for heavy report exports; empty uses the primary.
    database_read_url: str = ""
    # Connections each API process keeps open (min) and may open at peak (max).
    db_pool_min: int = 5
    db_pool_max: int = 20
    worker_database_url: str = ""
    migrations_database_url: str = "postgresql+asyncpg://osa_migrator:local-only@localhost:5432/osa"
    redis_state_url: str = "redis://:local-only@localhost:6379/0"
    redis_cache_url: str = "redis://:local-only@localhost:6379/1"
    jwt_secret: str = "development-only-signing-secret-change-before-deploy"
    jwt_kid: str = "k1"
    # During a signing-key rotation, tokens signed with the previous key are still accepted
    # until they expire (docs/runbooks/key-rotation.md). Empty outside a rotation.
    jwt_previous_secret: str = ""
    jwt_previous_kid: str = ""
    jwt_issuer: str = "opd-saathi"
    jwt_audience: str = "opd-saathi-api"
    cursor_hmac_key: str = "development-only-cursor-key-change-before-deploy"
    app_data_key: str = ""
    # Key ring for rotating APP_DATA_KEY: the current key's id, and earlier keys still needed
    # to read old records, as "id:base64,id:base64" (docs/runbooks/key-rotation.md).
    app_data_key_id: str = "1"
    app_data_previous_keys: str = ""
    app_hmac_key: str = ""
    # Encrypts only the phone numbers of patients who opted in to queue messages. The worker
    # holds this key and never APP_DATA_KEY, so a compromised worker exposes nothing clinical.
    notify_key: str = ""
    # "auto" picks the best configured provider: Sarvam, then Gemini, then the browser.
    stt_provider: str = "auto"
    stt_model: str = "saaras:v3-realtime"
    sarvam_api_key: str = ""
    tts_provider: str = "auto"
    tts_model: str = "bulbul:v3"
    tts_voice: str = ""
    gemini_tts_model: str = "gemini-3.8-flash-tts"
    gemini_tts_voice: str = "Kore"
    store_audio: bool = False
    notification_provider: str = "console"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    llm_provider: str = "auto"
    llm_model: str = "gemini-3.5-flash-lite"
    llm_fallback_model: str = "gemini-3.1-flash-lite"
    llm_thinking_level: str = "minimal"
    # Let the model re-order follow-up questions (adds up to 1.5 s to each question).
    ai_question_choice: bool = False
    # Draft up to three complaint-specific follow-up questions with the language model.
    ai_followup_questions: bool = True
    gemini_api_key: str = ""
    llm_enabled: bool = True
    llm_max_calls_per_session: int = 30
    llm_max_output_tokens: int = 1024
    access_token_minutes: int = 10
    refresh_token_hours: int = 8
    kiosk_token_hours: int = 12
    # How long a one-time kiosk activation code can be used before an admin must issue another.
    kiosk_activation_code_hours: int = 24
    # A kiosk's device secret is replaced this often, at a token renewal (task 7.3.9).
    kiosk_secret_rotation_days: int = 30
    patient_session_hours: int = 2
    max_request_body_bytes: int = 1_048_576
    # Lets the staff sign-in page offer a hospital picker instead of a raw ID field.
    public_hospital_directory: bool = True
    metrics_enabled: bool = False
    urgent_bonus_min: int = 360
    priority_bonus_min: int = 45
    vulnerable_bonus_min: int = 15
    default_consult_min: float = 4.0

    # Roles that must use two-step sign-in (ADR 008); doctors and nurses may opt in.
    mfa_required_roles: str = "admin"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _FileReferenceSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production")
        if self.provider_mode not in {"standard", "demo-safe"}:
            raise ValueError("PROVIDER_MODE must be standard or demo-safe")
        if self.demo_story not in {"hindi-fever", "marathi-chest-pain", "english-child-cough"}:
            raise ValueError("DEMO_STORY must name a configured synthetic demo story")
        if self.stt_provider not in {"auto", "mock", "sarvam", "gemini", "browser"}:
            raise ValueError("STT_PROVIDER must be auto, mock, sarvam, gemini, or browser")
        if self.tts_provider not in {"auto", "mock", "sarvam", "google", "gemini", "browser"}:
            raise ValueError("TTS_PROVIDER must be auto, mock, sarvam, google, gemini, or browser")
        if self.llm_provider not in {"auto", "mock", "gemini"}:
            raise ValueError("LLM_PROVIDER must be auto, mock, or gemini")
        if self.llm_thinking_level not in {"", "minimal", "low", "medium", "high"}:
            raise ValueError("LLM_THINKING_LEVEL must be minimal, low, medium, high, or empty")
        if self.stt_provider == "gemini" and not self.gemini_api_key.strip():
            raise ValueError("GEMINI_API_KEY is required when STT_PROVIDER=gemini")
        if self.tts_provider == "gemini" and not self.gemini_api_key.strip():
            raise ValueError("GEMINI_API_KEY is required when TTS_PROVIDER=gemini")
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
        if self.notify_key:
            notify_key = _decode_key(self.notify_key, "NOTIFY_KEY")
            try:
                same_key = notify_key == base64.b64decode(self.app_data_key, validate=True)
            except (binascii.Error, ValueError):
                same_key = False
            if same_key:
                raise ValueError("NOTIFY_KEY must differ from APP_DATA_KEY")
        elif self.notification_provider != "console":
            raise ValueError("NOTIFY_KEY is required when NOTIFICATION_PROVIDER sends messages")
        if not 1 <= self.llm_max_calls_per_session <= 30:
            raise ValueError("LLM_MAX_CALLS_PER_SESSION must be between 1 and 30")
        if not 1 <= self.llm_max_output_tokens <= 1024:
            raise ValueError("LLM_MAX_OUTPUT_TOKENS must be between 1 and 1024")
        if (
            self.provider_mode != "demo-safe"
            and self.llm_provider == "gemini"
            and not self.gemini_api_key.strip()
        ):
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        if (
            self.provider_mode != "demo-safe"
            and self.tts_provider == "sarvam"
            and not self.sarvam_api_key.strip()
        ):
            raise ValueError("SARVAM_API_KEY is required when TTS_PROVIDER=sarvam")
        if self.access_token_minutes != 10:
            raise ValueError("staff access tokens must expire after 10 minutes")
        if (
            min(
                self.refresh_token_hours,
                self.kiosk_token_hours,
                self.kiosk_activation_code_hours,
                self.patient_session_hours,
            )
            < 1
        ):
            raise ValueError(
                "refresh, kiosk, activation-code, and patient token lifetimes must be positive"
            )
        if not self.jwt_issuer.strip() or not self.jwt_audience.strip():
            raise ValueError("JWT_ISSUER and JWT_AUDIENCE must not be empty")
        if self.app_env == "production" and (
            len(self.jwt_secret.encode("utf-8")) < 32
            or self.jwt_secret.startswith("development-only-")
        ):
            raise ValueError("production JWT_SECRET must contain at least 32 random bytes")
        if self.app_env == "production" and len(self.cursor_hmac_key.encode("utf-8")) < 32:
            raise ValueError("production CURSOR_HMAC_KEY must contain at least 32 random bytes")
        if self.process_role not in {"api", "worker"}:
            raise ValueError("PROCESS_ROLE must be api or worker")
        if self.app_env == "production" and self.process_role == "worker":
            # The worker never holds patient-data keys (task 7.0.1): refuse to start if given.
            if self.app_data_key or self.app_hmac_key:
                raise ValueError(
                    "the production worker must not be given APP_DATA_KEY or APP_HMAC_KEY"
                )
        elif self.app_env == "production":
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
        if self.jwt_previous_secret and (
            not self.jwt_previous_kid or self.jwt_previous_kid == self.jwt_kid
        ):
            raise ValueError("JWT_PREVIOUS_KID must name the previous key and differ from JWT_KID")
        if not set(self.mfa_required_role_set) <= set(STAFF_ROLES):
            raise ValueError("MFA_REQUIRED_ROLES may list only admin, doctor and nurse")
        if self.app_env == "production" and "admin" not in self.mfa_required_role_set:
            raise ValueError("administrators must use two-step sign-in in production")
        key_id = self.app_data_key_id
        if not key_id or len(key_id) > 16 or ":" in key_id:
            raise ValueError("APP_DATA_KEY_ID must have 1-16 characters and no colon")
        self._previous_data_keys()
        if not self.jwt_kid or len(self.jwt_kid) > 64:
            raise ValueError("JWT_KID must contain between 1 and 64 characters")
        if not 1 <= self.kiosk_secret_rotation_days <= 365:
            raise ValueError("KIOSK_SECRET_ROTATION_DAYS must be between 1 and 365")
        if not 1 <= self.db_pool_min <= self.db_pool_max <= 200:
            raise ValueError("DB_POOL_MIN and DB_POOL_MAX must satisfy 1 <= min <= max <= 200")
        if self.max_request_body_bytes < 1024:
            raise ValueError("MAX_REQUEST_BODY_BYTES must be at least 1024")
        if min(self.urgent_bonus_min, self.priority_bonus_min, self.vulnerable_bonus_min) < 0:
            raise ValueError("queue score bonuses cannot be negative")
        if not 1 <= self.default_consult_min <= 15:
            raise ValueError("DEFAULT_CONSULT_MIN must be between 1 and 15")
        return self

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider == "auto":
            return "gemini" if self.gemini_api_key.strip() else "mock"
        return self.llm_provider

    @property
    def resolved_stt_provider(self) -> str:
        """Server-side speech recognition; "browser" means the kiosk browser does it."""
        if self.stt_provider != "auto":
            return self.stt_provider
        if self.sarvam_api_key.strip():
            return "sarvam"
        return "gemini" if self.gemini_api_key.strip() else "browser"

    @property
    def resolved_tts_provider(self) -> str:
        if self.tts_provider != "auto":
            return self.tts_provider
        if self.sarvam_api_key.strip():
            return "sarvam"
        return "gemini" if self.gemini_api_key.strip() else "browser"

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
    def mfa_required_role_set(self) -> frozenset[str]:
        # "none" (or empty) means no role is required to use it, e.g. the local demo.
        return frozenset(
            role.strip()
            for role in self.mfa_required_roles.split(",")
            if role.strip() and role.strip() != "none"
        )

    def _previous_data_keys(self) -> dict[str, bytes]:
        keys: dict[str, bytes] = {}
        for entry in self.app_data_previous_keys.split(","):
            if not entry.strip():
                continue
            key_id, separator, encoded = entry.strip().partition(":")
            if not separator or not key_id or key_id == self.app_data_key_id:
                raise ValueError("APP_DATA_PREVIOUS_KEYS entries are id:base64 with other ids")
            keys[key_id] = _decode_key(encoded, f"APP_DATA_PREVIOUS_KEYS[{key_id}]")
        return keys

    @property
    def app_data_keyring(self) -> "KeyRing":
        """The current data key, for writing, and every key that can still read old records."""
        from app.core.crypto import KeyRing

        return KeyRing(
            current_id=self.app_data_key_id,
            keys={**self._previous_data_keys(), self.app_data_key_id: self.app_data_key_bytes},
        )

    @property
    def app_hmac_key_bytes(self) -> bytes:
        return _decode_key(self.app_hmac_key, "APP_HMAC_KEY")

    @property
    def notify_key_bytes(self) -> bytes | None:
        """The opted-in phone key, or None where no messages are sent (console provider)."""
        return _decode_key(self.notify_key, "NOTIFY_KEY") if self.notify_key else None


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
