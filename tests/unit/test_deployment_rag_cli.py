import sys

import pytest

from enterprise_agent import deployment
from enterprise_agent.config import PROJECT_ROOT


@pytest.fixture
def ordinary_container(monkeypatch, tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name in ("postgres-app-password", "postgres-analytics-password", "jwt-secret"):
        (secrets / name).write_text("local-fixture-value-" + "a" * 32, encoding="utf-8")
    original = deployment.load_settings

    def load(mode, *, environment):
        return original(mode, secret_dir=secrets, environment=environment)

    monkeypatch.setattr(deployment, "load_settings", load)
    return tmp_path


@pytest.mark.parametrize("chat_enabled", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_rag_cli_needs_no_provider_or_owner_secrets(
    monkeypatch, ordinary_container, chat_enabled, success
):
    observed = {}

    async def evaluate(settings, *, dataset_path, output, top_k):
        observed.update(settings=settings, dataset=dataset_path, output=output, top_k=top_k)
        return success

    monkeypatch.setenv("EA_LLM_ENABLED", str(chat_enabled).lower())
    monkeypatch.setattr("enterprise_agent.evaluation.run_evaluation", evaluate)
    output = ordinary_container / "report"
    monkeypatch.setattr(sys, "argv", ["deployment", "rag-evaluate", "--output", str(output)])
    with pytest.raises(SystemExit) as exit_status:
        deployment.main()
    assert exit_status.value.code == (0 if success else 1)
    settings = observed["settings"]
    assert settings.app_env == "production"
    assert settings.llm_enabled is False
    assert settings.langsmith_enabled is False
    assert settings.deepseek_api_key is None
    assert settings.migration_database_url is None
    assert settings.test_database_url is None
    assert observed["dataset"] == PROJECT_ROOT / "evals/datasets/rag.json"
    assert observed["output"] == output
    assert observed["top_k"] == 5


def test_rag_cli_default_keeps_runs_separate(monkeypatch, ordinary_container):
    outputs = []

    async def evaluate(settings, *, dataset_path, output, top_k):
        outputs.append(output)
        assert output.parent == settings.data_dir / "evaluation/phase9"
        assert output.name.startswith("rag-")
        return True

    monkeypatch.setattr("enterprise_agent.evaluation.run_evaluation", evaluate)
    monkeypatch.setattr(sys, "argv", ["deployment", "rag-evaluate"])
    for _ in range(2):
        with pytest.raises(SystemExit) as exit_status:
            deployment.main()
        assert exit_status.value.code == 0
    assert outputs[0] != outputs[1]


def test_output_cannot_be_silently_ignored_by_serve(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["deployment", "serve", "--output", "/unused"])
    with pytest.raises(SystemExit) as exit_status:
        deployment.main()
    assert exit_status.value.code == 2
