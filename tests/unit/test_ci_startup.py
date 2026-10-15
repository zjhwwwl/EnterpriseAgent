"""Startup diagnostics fail closed and never serialize credential or error text."""

import importlib.util
import json
from types import SimpleNamespace

import pytest

from enterprise_agent.config import PROJECT_ROOT


@pytest.fixture
def startup(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "ci_startup_checks", PROJECT_ROOT / "scripts/ci/check_startup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_secret", lambda *args, **kwargs: "private-test-value")
    monkeypatch.setattr(
        module,
        "load_settings",
        lambda *args, **kwargs: SimpleNamespace(rag_model_dir=tmp_path, data_dir=tmp_path),
    )
    monkeypatch.setattr(module, "verify_model_assets", lambda path: True)
    return module


def test_valid_startup_reports_only_access_states(startup):
    result = startup.check_startup()
    assert result["status"] == "passed"
    assert result["configuration_valid"] is True
    assert result["runtime_directory_writable"] is True
    assert "private-test-value" not in json.dumps(result)


def test_unreadable_secret_and_configuration_error_do_not_disclose_exception_text(
    startup, monkeypatch
):
    private_error = "sensitive-credential-in-exception"

    def refused(*args, **kwargs):
        raise ValueError(private_error)

    monkeypatch.setattr(startup, "read_secret", refused)
    monkeypatch.setattr(startup, "load_settings", refused)
    result = startup.check_startup()
    assert result["status"] == "failed"
    assert result["configuration_valid"] is False
    assert not any(result["secret_readable"].values())
    assert private_error not in json.dumps(result)


def test_unverified_model_assets_fail_startup(startup, monkeypatch):
    monkeypatch.setattr(startup, "verify_model_assets", lambda path: False)
    result = startup.check_startup()
    assert result["status"] == "failed"
    assert result["pinned_models_verified"] is False


def test_unwritable_runtime_directory_fails_without_exposing_path(startup, monkeypatch):
    def refused(*args, **kwargs):
        raise PermissionError("private-runtime-path")

    monkeypatch.setattr(startup.tempfile, "NamedTemporaryFile", refused)
    result = startup.check_startup()
    assert result["status"] == "failed"
    assert result["runtime_directory_writable"] is False
    assert "private-runtime-path" not in json.dumps(result)
