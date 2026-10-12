"""Logs retain useful metrics without exposing credentials or exception payloads."""

import json
import logging
import sys
from datetime import datetime

import pytest
from pydantic import SecretStr

from enterprise_agent.logging_config import (
    JsonFormatter,
    configure_logging,
    redact_sensitive,
)


def test_recursive_redaction_preserves_metrics_and_useful_context():
    secret_values = ("fake-configured-secret",)
    payload = {
        "password": "fake-password",
        "api_key": "fake-api-key",
        "authorization": "Bearer fake-auth-token",
        "nested": [
            {
                "credential": SecretStr("fake-secret-str"),
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "token_count": 160,
                "message": "request uses fake-configured-secret",
            }
        ],
        "safe": "order lookup completed",
    }

    redacted = redact_sensitive(payload, secret_values=secret_values)
    serialized = json.dumps(redacted)

    for value in (
        "fake-password",
        "fake-api-key",
        "fake-auth-token",
        "fake-secret-str",
        "fake-configured-secret",
    ):
        assert value not in serialized
    assert redacted["nested"][0]["prompt_tokens"] == 120
    assert redacted["nested"][0]["completion_tokens"] == 40
    assert redacted["nested"][0]["token_count"] == 160
    assert redacted["safe"] == "order lookup completed"
    assert payload["password"] == "fake-password", "redaction must not mutate caller data"


def test_redacts_credential_patterns_embedded_in_messages():
    text = (
        "Authorization: Bearer fake-bearer-secret; key sk-abcdefghijklmnopqrstuv; "
        "database postgresql://demo:fake-url-password@localhost/demo"
    )

    redacted = redact_sensitive(text)

    assert "fake-bearer-secret" not in redacted
    assert "sk-abcdefghijklmnopqrstuv" not in redacted
    assert "fake-url-password" not in redacted


@pytest.mark.parametrize(
    ("message", "arguments"),
    [
        ("payload=%s", ({"password": "fake-unregistered-private"},)),
        ('payload={"api_key":"fake-unregistered-private"}', ()),
        ("payload={'password': 'fake-unregistered-private'}", ()),
        ("credential=%(api_key)s", {"api_key": "fake-unregistered-private"}),
    ],
)
def test_formatted_messages_redact_nested_or_quoted_credentials(message, arguments):
    record = logging.makeLogRecord({"msg": message, "args": arguments, "levelname": "INFO"})
    output = JsonFormatter().format(record)
    assert "fake-unregistered-private" not in output
    assert "REDACTED" in output


@pytest.mark.parametrize(
    ("message", "arguments"),
    [
        ("secret=%d", (SecretStr("fake-formatting-secret"),)),
        ("secret=%(missing)s", {"api_key": "fake-formatting-secret"}),
    ],
)
def test_malformed_log_format_does_not_trigger_raw_record_dump(message, arguments):
    record = logging.makeLogRecord({"msg": message, "args": arguments, "levelname": "INFO"})
    output = JsonFormatter().format(record)
    assert json.loads(output)["message"] == "Log message formatting failed"
    assert "fake-formatting-secret" not in output


def test_non_finite_metrics_remain_valid_json_without_logging_errors():
    record = logging.makeLogRecord({"msg": "metric", "duration_ms": float("nan")})
    assert json.loads(JsonFormatter().format(record))["duration_ms"] is None


def test_json_formatter_serializes_structured_context_and_utc_timestamp():
    record = logging.makeLogRecord(
        {
            "name": "enterprise_agent.test",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "completed %s",
            "args": ("request",),
            "request_id": "request-123",
            "duration_ms": 12.5,
            "details": {"order_number": "10086", "api_key": "fake-nested-key"},
        }
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "enterprise_agent.test"
    assert payload["message"] == "completed request"
    assert payload["request_id"] == "request-123"
    timestamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
    assert timestamp.utcoffset().total_seconds() == 0
    assert payload["duration_ms"] == 12.5
    assert payload["details"]["order_number"] == "10086"
    assert "fake-nested-key" not in json.dumps(payload)


def test_formatter_does_not_output_raw_exception_or_traceback():
    try:
        raise RuntimeError("fake-sensitive-exception-payload")
    except RuntimeError:
        record = logging.makeLogRecord(
            {
                "name": "enterprise_agent.test",
                "levelno": logging.ERROR,
                "levelname": "ERROR",
                "msg": "operation failed",
                "args": (),
                "exc_info": sys.exc_info(),
                "exception": RuntimeError("fake-sensitive-extra-exception"),
            }
        )

    serialized = JsonFormatter().format(record)
    payload = json.loads(serialized)

    assert payload["message"] == "operation failed"
    assert "fake-sensitive-exception-payload" not in serialized
    assert "fake-sensitive-extra-exception" not in serialized
    assert "Traceback" not in serialized


def test_configured_logging_emits_json_and_redacts_registered_secret(capsys):
    logger_states = [
        (logger, logger.handlers[:], logger.level, logger.propagate)
        for logger in (
            logging.getLogger("enterprise_agent"),
            logging.getLogger("uvicorn"),
            logging.getLogger("uvicorn.error"),
            logging.getLogger("uvicorn.access"),
        )
    ]
    try:
        configure_logging("INFO", secret_values=("fake-registered-secret",))
        configured = logging.getLogger("enterprise_agent")
        logging.getLogger("enterprise_agent.logging_test").info(
            "configured fake-registered-secret",
            extra={"request_id": "log-request", "token_count": 7},
        )
        output = capsys.readouterr()
        lines = (output.out + output.err).strip().splitlines()

        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["request_id"] == "log-request"
        assert payload["token_count"] == 7
        assert "fake-registered-secret" not in lines[0]
        assert configured.level == logging.INFO
        assert any(isinstance(handler.formatter, JsonFormatter) for handler in configured.handlers)
    finally:
        for logger, handlers, level, propagate in logger_states:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
