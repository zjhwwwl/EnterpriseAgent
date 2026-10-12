"""Configuration checks cover startup mistakes and accidental credential exposure."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from enterprise_agent.config import PROJECT_ROOT, Settings, get_settings


def test_defaults_start_without_external_services_or_model_credentials():
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert str(settings.host) == "127.0.0.1"
    assert settings.llm_enabled is False
    assert settings.deepseek_api_key is None
    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.data_dir == (PROJECT_ROOT / "var").resolve()


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_known_environments_are_accepted(environment):
    assert Settings(_env_file=None, app_env=environment).app_env == environment


@pytest.mark.parametrize(
    "overrides",
    [
        {"app_env": "staging"},
        {"host": "0.0.0.999"},
        {"host": "localhost"},
        {"port": 0},
        {"port": 65536},
        {"log_level": "TRACE"},
    ],
)
def test_invalid_configuration_fails_before_serving_requests(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_ipv6_host_and_boundary_ports_are_valid():
    assert str(Settings(_env_file=None, host="::1", port=1).host) == "::1"
    assert Settings(_env_file=None, port=65535).port == 65535


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error", "critical"])
def test_log_levels_are_normalized(level):
    assert Settings(_env_file=None, log_level=level).log_level == level.upper()


def test_relative_data_directory_is_resolved_from_project_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    settings = Settings(_env_file=None, data_dir=Path("var") / "uploads")

    assert settings.data_dir == (PROJECT_ROOT / "var" / "uploads").resolve()


def test_absolute_data_directory_is_preserved(tmp_path):
    assert Settings(_env_file=None, data_dir=tmp_path).data_dir == tmp_path.resolve()


def test_valid_cors_origins_are_retained():
    origins = ["http://localhost:5173", "https://dashboard.example.com"]
    assert Settings(_env_file=None, cors_origins=origins).cors_origins == origins


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "ftp://dashboard.example.com",
        "https://user:password@dashboard.example.com",
        "https://dashboard.example.com/admin",
        "https://dashboard.example.com?token=secret",
        "https://dashboard.example.com#fragment",
        "dashboard.example.com",
    ],
)
def test_cors_requires_explicit_http_origins(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


@pytest.mark.parametrize("name", ["deepseek_api_key", "database_url", "redis_url"])
def test_blank_optional_credentials_are_unset(name):
    assert getattr(Settings(_env_file=None, **{name: ""}), name) is None


def test_enabled_model_requires_api_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_enabled=True)


def test_api_key_remains_a_secret_even_when_model_is_disabled():
    credential = "fake-config-secret-value"
    settings = Settings(_env_file=None, deepseek_api_key=credential)

    assert isinstance(settings.deepseek_api_key, SecretStr)
    assert settings.deepseek_api_key.get_secret_value() == credential
    assert credential not in repr(settings)
    assert credential not in settings.model_dump_json()


def test_enabled_model_accepts_configured_key():
    settings = Settings(_env_file=None, llm_enabled=True, deepseek_api_key="fake-key")
    assert settings.llm_enabled is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://api.example.com",
        "https://user:password@api.example.com/v1",
        "https://api.example.com/v1?api_key=secret",
        "https://api.example.com/v1#fragment",
    ],
)
def test_model_endpoint_rejects_credential_bearing_or_invalid_urls(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deepseek_base_url=url)


def test_model_endpoint_allows_openai_compatible_api_path():
    settings = Settings(_env_file=None, deepseek_base_url="https://api.deepseek.com/v1")
    assert str(settings.deepseek_base_url) == "https://api.deepseek.com/v1"


def test_environment_takes_precedence_over_dotenv_and_ignores_unknown_fields(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("EA_PORT=8100\nEA_APP_ENV=test\nUNRELATED_SETTING=ignore\n", encoding="utf-8")
    monkeypatch.setenv("EA_PORT", "8200")
    monkeypatch.setenv("PORT", "8300")

    settings = Settings(_env_file=dotenv)

    assert settings.port == 8200
    assert settings.app_env == "test"


def test_settings_cache_is_stable_until_explicitly_cleared(monkeypatch):
    monkeypatch.setattr("enterprise_agent.config.Settings", lambda: Settings(_env_file=None))
    monkeypatch.setenv("EA_PORT", "8100")
    monkeypatch.setenv("EA_LLM_ENABLED", "false")
    first = get_settings()
    monkeypatch.setenv("EA_PORT", "8200")

    assert get_settings() is first
    assert get_settings().port == 8100

    get_settings.cache_clear()
    assert get_settings().port == 8200


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "sqlite:///demo.db", "jwt_secret": "x" * 32},
        {"database_url": "postgresql+psycopg://localhost/demo"},
        {"jwt_secret": "too-short"},
        {"login_max_attempts": 0},
        {"jwt_expire_minutes": 121},
    ],
)
def test_business_configuration_fails_closed(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)
