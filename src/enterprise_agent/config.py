"""Validated environment settings; no network or filesystem writes at import."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import unquote, urlsplit

from pydantic import AnyHttpUrl, Field, IPvAnyAddress, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EA_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    host: IPvAnyAddress = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    docs_enabled: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    data_dir: Path = PROJECT_ROOT / "var"

    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    test_database_url: SecretStr | None = None
    test_migration_database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    jwt_expire_minutes: int = Field(default=30, ge=1, le=120)
    demo_password: SecretStr = "DemoOnly!2026"
    login_max_attempts: int = Field(default=10, ge=1, le=100)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)
    llm_enabled: bool = False
    deepseek_base_url: AnyHttpUrl = "https://api.deepseek.com"
    deepseek_api_key: SecretStr | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "database_url",
        "migration_database_url",
        "test_database_url",
        "test_migration_database_url",
        "redis_url",
        "jwt_secret",
        "deepseek_api_key",
        mode="before",
    )
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            url = AnyHttpUrl(value)
            if (
                url.username is not None
                or url.password is not None
                or url.path not in (None, "/")
                or url.query is not None
                or url.fragment is not None
                or url.host == "*"
            ):
                raise ValueError("CORS entries must be HTTP origins without credentials or paths")
            origin = str(url).rstrip("/")
            if origin not in result:
                result.append(origin)
        return result

    @field_validator("deepseek_base_url")
    @classmethod
    def validate_model_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        parts = (value.username, value.password, value.query, value.fragment)
        if any(part is not None for part in parts):
            raise ValueError("The model URL must not contain credentials, query, or fragment")
        return value

    @model_validator(mode="after")
    def validate_llm_configuration(self) -> Self:
        if self.llm_enabled and self.deepseek_api_key is None:
            raise ValueError("EA_DEEPSEEK_API_KEY is required when EA_LLM_ENABLED is true")
        if self.jwt_secret is not None and len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("EA_JWT_SECRET must contain at least 32 characters")
        if self.database_url is not None and self.jwt_secret is None:
            raise ValueError("EA_JWT_SECRET is required when a business database is configured")
        return self

    @field_validator(
        "database_url", "migration_database_url", "test_database_url", "test_migration_database_url"
    )
    @classmethod
    def validate_postgres_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            url = urlsplit(value.get_secret_value())
            if url.scheme != "postgresql+psycopg" or not url.hostname or not url.path.strip("/"):
                raise ValueError(
                    "Database URLs must use postgresql+psycopg with a host and database"
                )
        return value

    def secret_values(self) -> tuple[str, ...]:
        """Provide configured secrets to the logger, never to an API response."""
        values: set[str] = set()
        for secret in (
            self.database_url,
            self.migration_database_url,
            self.test_database_url,
            self.test_migration_database_url,
            self.redis_url,
            self.jwt_secret,
            self.demo_password,
            self.deepseek_api_key,
        ):
            if secret is not None:
                raw = secret.get_secret_value()
                values.add(raw)
                try:
                    password = urlsplit(raw).password
                except ValueError:
                    password = None
                if password:
                    values.add(unquote(password))
        return tuple(sorted(values, key=len, reverse=True))


@lru_cache
def get_settings() -> Settings:
    return Settings()
