"""JSON logging with recursive redaction; bodies and raw prompts are not logged."""

import json
import logging
import math
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from pydantic import SecretBytes, SecretStr

from enterprise_agent.request_context import get_request_id

REDACTED = "[REDACTED]"
_standard_fields = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_sensitive_keys = re.compile(
    r"(?:password|passwd|secret|token|jwt|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"authorization|cookie|credentials?|database_url|redis_url|connection_string)\Z",
    re.IGNORECASE,
)
_sensitive_suffixes = ("_password", "_secret", "_api_key", "_token")
_credentials_in_url = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/@]+@", re.IGNORECASE)
_bearer = re.compile(r"\bBearer\s+[^\s,;\"']+", re.IGNORECASE)
_api_key = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_named_secret = re.compile(
    r"\b(password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie)"
    r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)


def _redact_text(value: str, secret_values: tuple[str, ...]) -> str:
    for secret in secret_values:
        if secret:
            value = value.replace(secret, REDACTED)
    value = _credentials_in_url.sub(lambda match: match.group(1) + REDACTED + "@", value)
    value = _bearer.sub("Bearer " + REDACTED, value)
    value = _api_key.sub(REDACTED, value)
    return _named_secret.sub(lambda match: match.group(1) + "=" + REDACTED, value)


def redact_sensitive(value: Any, *, secret_values: Iterable[str] = ()) -> Any:
    """Return JSON-safe data while keeping numerical token counters."""
    secrets = tuple(secret_values)
    if isinstance(value, (SecretStr, SecretBytes)):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED
                if _sensitive_keys.search(str(key))
                or str(key).lower().endswith(_sensitive_suffixes)
                else redact_sensitive(item, secret_values=secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_sensitive(item, secret_values=secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if isinstance(value, Path):
        return _redact_text(str(value), secrets)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


class JsonFormatter(logging.Formatter):
    def __init__(self, *, secret_values: Iterable[str] = ()) -> None:
        super().__init__()
        self.secret_values = tuple(secret_values)

    def format(self, record: logging.LogRecord) -> str:
        message = record.msg if isinstance(record.msg, str) else redact_sensitive(record.msg)
        if record.args:
            arguments = redact_sensitive(record.args, secret_values=self.secret_values)
            if isinstance(record.args, tuple):
                arguments = tuple(arguments)
            try:
                message = str(message) % arguments
            except (TypeError, ValueError, KeyError):
                # logging.handleError would print the original record and args.
                # Fail closed without passing raw credentials to stderr.
                message = "Log message formatting failed"
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": get_request_id() or getattr(record, "request_id", None),
        }
        for key, value in record.__dict__.items():
            if key not in _standard_fields and key not in payload:
                payload[key] = value
        if record.exc_info and record.exc_info[0]:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(
            redact_sensitive(payload, secret_values=self.secret_values),
            ensure_ascii=False,
            allow_nan=False,
        )


def configure_logging(
    level: str = "INFO", *, secret_values: Iterable[str] = (), stream: TextIO | None = None
) -> None:
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setFormatter(JsonFormatter(secret_values=secret_values))
    for name in ("enterprise_agent", "uvicorn"):
        configured = logging.getLogger(name)
        configured.handlers = [handler]
        configured.setLevel(level)
        configured.propagate = False
    logging.getLogger("uvicorn.error").handlers.clear()
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
