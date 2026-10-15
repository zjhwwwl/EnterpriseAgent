"""Public startup reports expose fixed state codes, never private failure data."""

import importlib.util
import json
from pathlib import Path

import pytest

from enterprise_agent.config import PROJECT_ROOT
from enterprise_agent.deployment import SETUP_STAGES, safe_setup_status, write_setup_status

PRIVATE_DSN = "postgresql+psycopg://private-user:private-password@private-host/private-db"
PUBLIC_FIELDS = {"status", "stage", "database_ready", "rag_ready"}


@pytest.fixture
def status_reader():
    spec = importlib.util.spec_from_file_location(
        "ci_setup_status_reader", PROJECT_ROOT / "scripts/ci/read_setup_status.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("stage", sorted(SETUP_STAGES))
@pytest.mark.parametrize("status", ["passed", "failed"])
def test_safe_status_preserves_only_fixed_states(stage, status):
    assert safe_setup_status(
        {"status": status, "stage": stage, "database_ready": True, "rag_ready": False}
    ) == {
        "status": status,
        "stage": stage,
        "database_ready": True,
        "rag_ready": False,
    }


def test_private_exceptions_credentials_and_extra_fields_are_not_serialized():
    result = safe_setup_status(
        {
            "status": "failed",
            "stage": "dependencies",
            "database_ready": False,
            "rag_ready": True,
            "exception": ValueError(PRIVATE_DSN),
            "message": PRIVATE_DSN,
            "database_url": PRIVATE_DSN,
            "details": {"api_key": "private-test-key"},
        }
    )
    assert set(result) == PUBLIC_FIELDS
    serialized = json.dumps(result)
    assert PRIVATE_DSN not in serialized
    assert "private-test-key" not in serialized
    assert "ValueError" not in serialized


@pytest.mark.parametrize(
    "stage", [PRIVATE_DSN, "seed\n" + PRIVATE_DSN, "COMPLETE", "", None, [], {}, True, 7]
)
def test_unknown_or_forged_stages_are_replaced_with_fixed_unknown(stage):
    result = safe_setup_status({"status": "passed", "stage": stage})
    assert result == {
        "status": "passed",
        "stage": "unknown",
        "database_ready": None,
        "rag_ready": None,
    }


@pytest.mark.parametrize("status", [PRIVATE_DSN, "PASSED", None, True, 1, [], {}])
def test_unrecognized_success_values_fail_closed(status):
    result = safe_setup_status({"status": status, "stage": "resources"})
    assert result["status"] == "failed"
    assert PRIVATE_DSN not in json.dumps(result)


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_readiness_requires_actual_boolean_values(value):
    result = safe_setup_status(
        {"status": "failed", "stage": "dependencies", "database_ready": value, "rag_ready": value}
    )
    assert result["database_ready"] is None
    assert result["rag_ready"] is None


def test_writer_persists_only_sanitized_report_and_replaces_previous_file(tmp_path):
    target = tmp_path / "setup-status.json"
    target.write_text(json.dumps({"message": PRIVATE_DSN}), encoding="utf-8")
    assert write_setup_status(
        tmp_path,
        {
            "status": "failed",
            "stage": "migrations",
            "error": ValueError(PRIVATE_DSN),
            "database_url": PRIVATE_DSN,
        },
    )
    result = json.loads(target.read_text(encoding="utf-8"))
    assert set(result) == PUBLIC_FIELDS
    assert result["stage"] == "migrations"
    assert PRIVATE_DSN not in target.read_text(encoding="utf-8")
    assert not (tmp_path / "setup-status.json.tmp").exists()


def test_writer_returns_false_without_exposing_private_permission_error(tmp_path, monkeypatch):
    def refused(*args, **kwargs):
        raise PermissionError(PRIVATE_DSN)

    monkeypatch.setattr(Path, "write_text", refused)
    assert write_setup_status(tmp_path, {"status": "failed", "stage": "seed"}) is False


def test_reader_preserves_public_fields_and_discards_private_payload(status_reader, tmp_path):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "status": "passed",
                "stage": "complete",
                "database_ready": True,
                "rag_ready": False,
                "database_url": PRIVATE_DSN,
                "error": {"message": PRIVATE_DSN},
            }
        ),
        encoding="utf-8",
    )
    assert status_reader.read_setup_status(path) == {
        "status": "passed",
        "stage": "complete",
        "database_ready": True,
        "rag_ready": False,
    }


def test_reader_rejects_forged_stage_and_nonboolean_readiness(status_reader, tmp_path):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {"status": PRIVATE_DSN, "stage": PRIVATE_DSN, "database_ready": 1, "rag_ready": "true"}
        ),
        encoding="utf-8",
    )
    assert status_reader.read_setup_status(path) == {
        "status": "failed",
        "stage": "unknown",
        "database_ready": None,
        "rag_ready": None,
    }


@pytest.mark.parametrize("stage", [[], {}])
def test_reader_does_not_expose_unhashable_forged_stage(status_reader, tmp_path, stage):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"status": "passed", "stage": stage}), encoding="utf-8")
    assert status_reader.read_setup_status(path) == {
        "status": "passed",
        "stage": "unknown",
        "database_ready": None,
        "rag_ready": None,
    }


def test_reader_accepts_exactly_four_kib_but_rejects_larger_file(status_reader, tmp_path):
    path = tmp_path / "status.json"
    payload = json.dumps({"status": "passed", "stage": "complete"}).encode()
    path.write_bytes(payload + b" " * (4096 - len(payload)))
    assert status_reader.read_setup_status(path)["status"] == "passed"
    path.write_bytes(path.read_bytes() + b" ")
    assert status_reader.read_setup_status(path) == {"status": "unavailable"}


@pytest.mark.parametrize("payload", [b"", b"{", b"\xff", b"[]", b"null", b'"private-error"'])
def test_reader_returns_unavailable_for_invalid_reports(status_reader, tmp_path, payload):
    path = tmp_path / "status.json"
    path.write_bytes(payload)
    assert status_reader.read_setup_status(path) == {"status": "unavailable"}


def test_reader_returns_unavailable_for_missing_file(status_reader, tmp_path):
    assert status_reader.read_setup_status(tmp_path / "missing.json") == {"status": "unavailable"}


def test_reader_hides_private_read_permission_error(status_reader, tmp_path, monkeypatch):
    def refused(*args, **kwargs):
        raise PermissionError(PRIVATE_DSN)

    monkeypatch.setattr(Path, "open", refused)
    assert status_reader.read_setup_status(tmp_path / "status.json") == {"status": "unavailable"}
