"""Deployment boundaries are checked without Docker, provider calls or a database."""

from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from enterprise_agent.config import PROJECT_ROOT
from enterprise_agent.deployment import (
    DeploymentSettings,
    database_url,
    load_settings,
    read_secret,
    run_tests,
)


@pytest.fixture
def secret_dir(tmp_path):
    for name in (
        "postgres-app-password",
        "postgres-superuser-password",
        "postgres-test-password",
        "postgres-analytics-password",
        "postgres-test-analytics-password",
        "jwt-secret",
        "demo-password",
        "deepseek-api-key",
    ):
        (tmp_path / name).write_text("a" * 64, encoding="utf-8")
    return tmp_path


def test_ordinary_api_never_loads_owner_or_test_secrets(secret_dir):
    for name in (
        "postgres-superuser-password",
        "postgres-test-password",
        "postgres-test-analytics-password",
        "demo-password",
        "deepseek-api-key",
    ):
        (secret_dir / name).unlink()
    settings = load_settings("serve", secret_dir=secret_dir, environment={})
    assert settings.app_env == "production"
    assert make_url(settings.database_url.get_secret_value()).username == "ea_app"
    assert (
        make_url(settings.analytics_database_url.get_secret_value()).username
        == "ea_analytics_reader"
    )
    assert settings.migration_database_url is None
    assert settings.test_database_url is None
    assert settings.test_migration_database_url is None
    assert settings.test_analytics_database_url is None
    assert settings.deepseek_api_key is None
    assert settings.llm_enabled is False
    assert settings.langsmith_enabled is False
    assert settings.rag_enabled is True
    assert settings.analytics_enabled is True
    assert settings.docs_enabled is False


@pytest.mark.parametrize("mode", ["setup", "tests"])
def test_owner_is_limited_to_initialization_and_dedicated_tests(secret_dir, mode):
    settings = load_settings(mode, secret_dir=secret_dir, environment={})
    owner = make_url(settings.migration_database_url.get_secret_value())
    assert owner.username == "ea_local_admin"
    assert owner.host == "postgres"
    assert owner.database == ("enterprise_agent_test" if mode == "tests" else "enterprise_agent")
    if mode == "tests":
        assert make_url(settings.test_database_url.get_secret_value()).username == "ea_test_app"
        assert (
            make_url(settings.test_analytics_database_url.get_secret_value()).username
            == "ea_test_analytics_reader"
        )
    else:
        assert settings.test_database_url is None


@pytest.mark.parametrize(
    "name",
    [
        "EA_DATABASE_URL",
        "EA_MIGRATION_DATABASE_URL",
        "EA_TEST_DATABASE_URL",
        "EA_TEST_MIGRATION_DATABASE_URL",
        "EA_ANALYTICS_DATABASE_URL",
        "EA_TEST_ANALYTICS_DATABASE_URL",
        "EA_REDIS_URL",
        "EA_JWT_SECRET",
        "EA_DEMO_PASSWORD",
        "EA_DEEPSEEK_API_KEY",
        "EA_LANGSMITH_API_KEY",
        "EA_MCP_ACCESS_TOKEN",
    ],
)
def test_inline_secrets_are_refused_without_echoing_values(secret_dir, name):
    value = "sensitive-value-that-must-not-appear"
    with pytest.raises(ValueError, match="secret files") as error:
        load_settings("serve", secret_dir=secret_dir, environment={name: value})
    assert value not in str(error.value)


def test_ambient_environment_and_dotenv_do_not_change_deployment_settings(monkeypatch):
    monkeypatch.setenv("EA_DEEPSEEK_API_KEY", "ambient-credential")
    monkeypatch.setenv("EA_DATABASE_URL", "postgresql+psycopg://root:bad@outside/live")
    monkeypatch.setenv("EA_RAG_ENABLED", "false")
    configured = DeploymentSettings(_env_file=PROJECT_ROOT / ".env")
    assert configured.database_url is None
    assert configured.deepseek_api_key is None
    assert configured.rag_enabled is False


def test_deployment_knobs_are_bounded_while_hosts_and_paths_stay_fixed(secret_dir):
    configured = load_settings(
        "serve",
        secret_dir=secret_dir,
        environment={
            "EA_RAG_MODEL_THREADS": "4",
            "EA_DOCS_ENABLED": "true",
            "EA_HOST": "203.0.113.1",
            "EA_CHROMA_HOST": "external.example",
            "EA_RAG_MODEL_DIR": "elsewhere",
        },
    )
    assert configured.rag_model_threads == 4
    assert configured.docs_enabled is True
    assert str(configured.host) == "0.0.0.0"
    assert configured.chroma_host == "chroma"
    assert configured.rag_model_dir == PROJECT_ROOT / "var" / "models"
    with pytest.raises(ValidationError):
        load_settings("serve", secret_dir=secret_dir, environment={"EA_RAG_MODEL_THREADS": "99"})


@pytest.mark.parametrize("mode", ["setup", "tests"])
def test_initialization_and_tests_cannot_enable_paid_models(secret_dir, mode):
    with pytest.raises(ValueError, match="refuse external model"):
        load_settings(mode, secret_dir=secret_dir, environment={"EA_LLM_ENABLED": "true"})


def test_live_model_opt_in_requires_a_nonempty_secret_file(secret_dir):
    environment = {"EA_LLM_ENABLED": "true"}
    (secret_dir / "deepseek-api-key").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="deepseek-api-key"):
        load_settings("serve", secret_dir=secret_dir, environment=environment)
    (secret_dir / "deepseek-api-key").write_text("local-test-key-only-123456", encoding="utf-8")
    configured = load_settings("serve", secret_dir=secret_dir, environment=environment)
    assert configured.llm_enabled is True
    assert configured.deepseek_api_key.get_secret_value() == "local-test-key-only-123456"


@pytest.mark.parametrize("value", ["1", "yes", "TRUE ", "", "enabled"])
def test_provider_enable_flags_are_strict(secret_dir, value):
    with pytest.raises(ValueError, match="true or false"):
        load_settings("serve", secret_dir=secret_dir, environment={"EA_LLM_ENABLED": value})


def test_remote_tracing_remains_explicitly_disabled(secret_dir):
    with pytest.raises(ValueError, match="Remote tracing is disabled"):
        load_settings("serve", secret_dir=secret_dir, environment={"EA_LANGSMITH_ENABLED": "true"})


@pytest.mark.parametrize("value", ["short", "a" * 4097, "a" * 20 + "\nsecret", "a" * 20 + "\0"])
def test_invalid_secret_files_fail_without_echoing_their_content(secret_dir, value):
    (secret_dir / "postgres-app-password").write_text(value, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        read_secret(secret_dir, "postgres-app-password")
    assert value not in str(error.value)


@pytest.mark.parametrize("name", ["../jwt-secret", "/etc/passwd", "unknown"])
def test_secret_names_cannot_escape_the_allowlist(secret_dir, name):
    with pytest.raises(ValueError, match="Unknown deployment secret"):
        read_secret(secret_dir, name)


def test_database_password_is_url_encoded(secret_dir):
    (secret_dir / "postgres-app-password").write_text("password/@:#?&abc123", encoding="utf-8")
    password = read_secret(secret_dir, "postgres-app-password")
    url = make_url(database_url("ea_app", password, "enterprise_agent").get_secret_value())
    assert url.password == "password/@:#?&abc123"
    assert url.host == "postgres"
    assert url.database == "enterprise_agent"


def test_compose_has_isolated_project_ports_data_and_role_specific_mounts():
    configuration = yaml.safe_load((PROJECT_ROOT / "deploy/compose.full.yaml").read_text())
    assert configuration["name"] == "enterpriseagent-full"
    services = configuration["services"]
    for name in ("postgres", "redis", "chroma"):
        assert "ports" not in services[name]
        assert "@sha256:" in services[name]["image"]
    assert services["api"]["ports"] == ["127.0.0.1:18000:8000"]
    assert services["web"]["ports"] == ["127.0.0.1:15173:8080"]
    api_credentials = {item["source"] for item in services["api"]["secrets"]}
    assert api_credentials == {
        "postgres_app_password",
        "postgres_analytics_password",
        "jwt_secret",
        "deepseek_api_key",
    }
    assert services["api"]["depends_on"]["setup"]["condition"] == "service_completed_successfully"
    assert services["tests"]["profiles"] == ["test"]
    assert configuration["networks"]["backend"]["internal"] is True
    for name in ("api", "setup", "tests"):
        model_mounts = [
            volume for volume in services[name]["volumes"] if volume.startswith("../var/models/")
        ]
        assert len(model_mounts) == 3
        assert all(volume.endswith(":ro") for volume in model_mounts)
    for name, specification in configuration["secrets"].items():
        assert specification["file"].startswith("../var/compose/secrets/"), name


def test_docker_context_is_an_allowlist_with_no_local_state():
    patterns = (PROJECT_ROOT / ".dockerignore").read_text().splitlines()
    assert "*" in patterns
    assert "!requirements/linux-base.lock" in patterns
    assert "!requirements/linux-dev.lock" in patterns
    assert not any(
        pattern.startswith("!var") or pattern.startswith("!.env") for pattern in patterns
    )
    for filename in ("deploy/Dockerfile.api", "deploy/Dockerfile.web"):
        content = (PROJECT_ROOT / filename).read_text()
        assert "COPY . " not in content
        assert "COPY .env" not in content
        assert "@sha256:" in content


def test_only_dedicated_test_subprocess_receives_fixed_container_scope(secret_dir, monkeypatch):
    configured = load_settings("tests", secret_dir=secret_dir, environment={})
    monkeypatch.setenv("EA_TEST_CONTAINER_HOST", "external.example")
    monkeypatch.setenv("EA_DEEPSEEK_API_KEY", "unexported-fixture-credential")
    observed = {}

    def run(arguments, *, cwd, env, check):
        observed.update(arguments=arguments, cwd=cwd, environment=env, check=check)
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr("enterprise_agent.deployment.subprocess.run", run)
    assert run_tests(configured, ["-q"]) == 7
    environment = observed["environment"]
    assert environment["EA_TEST_CONTAINER_HOST"] == "postgres"
    assert make_url(environment["EA_TEST_DATABASE_URL"]).database == "enterprise_agent_test"
    assert make_url(environment["EA_TEST_DATABASE_URL"]).username == "ea_test_app"
    assert environment["EA_LLM_ENABLED"] == "false"
    assert "EA_DEEPSEEK_API_KEY" not in environment
