"""Limit the multipart request stream before disk spooling can grow unbounded."""

from fastapi import Request
from starlette.exceptions import HTTPException

from enterprise_agent.api.errors import error_response


class UploadLimitMiddleware:
    def __init__(
        self,
        app,
        max_bytes: int,
        *,
        path_prefix=None,
        allowance=64 * 1024,
        error_code="document_too_large",
        message="上传请求超过大小限制",
    ):
        self.app = app
        self.max_bytes = max_bytes
        self.path_prefix = path_prefix
        self.allowance = allowance
        self.error_code = error_code
        self.message = message

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not (
                scope["path"].startswith(self.path_prefix)
                if self.path_prefix
                else scope["path"] == "/api/v1/knowledge/documents"
            )
        ):
            return await self.app(scope, receive, send)
        limit = self.max_bytes + self.allowance
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            response = error_response(
                Request(scope),
                status_code=413,
                code=self.error_code,
                message=self.message,
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
