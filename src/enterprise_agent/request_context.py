"""Per-request IDs with pure ASGI middleware and ContextVar."""

import logging
import re
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_valid_request_id = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z", re.ASCII)
logger = logging.getLogger("enterprise_agent.http")


def get_request_id() -> str | None:
    return _request_id.get()


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = next(
            (value.decode("latin-1") for key, value in scope["headers"] if key == b"x-request-id"),
            "",
        )
        request_id = supplied if _valid_request_id.fullmatch(supplied) else uuid4().hex
        token = _request_id.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (key, value) for key, value in message["headers"] if key != b"x-request-id"
                ]
                message = {
                    **message,
                    "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))],
                }
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route = getattr(scope.get("route"), "path", "<unmatched>")
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http_request",
                    "method": scope["method"],
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            _request_id.reset(token)
