"""Limit the multipart request stream before disk spooling can grow unbounded."""

from fastapi import Request
from starlette.exceptions import HTTPException

from enterprise_agent.api.errors import error_response


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/api/v1/knowledge/documents"
        ):
            return await self.app(scope, receive, send)
        limit = self.max_bytes + 64 * 1024  # bounded multipart headers/metadata allowance
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            response = error_response(
                Request(scope),
                status_code=413,
                code="document_too_large",
                message="上传请求超过大小限制",
            )
            return await response(scope, receive, send)
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, limited_receive, send)
