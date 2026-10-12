"""Safe HTTP errors: do not echo request input or exception text."""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.schemas.system import ErrorDetail, ErrorResponse, ValidationIssue

logger = logging.getLogger("enterprise_agent.errors")


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ValidationIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unavailable")
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details or []),
        request_id=request_id,
    )
    response = JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={**(headers or {}), "X-Request-ID": request_id},
    )
    # Starlette handles unexpected exceptions outside user middleware. Apply
    # the same explicit CORS policy here so the frontend can read safe 500s.
    if status_code >= 500:
        origin = request.headers.get("origin")
        if origin in request.app.state.settings.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
            response.headers["Vary"] = "Origin"
    return response


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    known = {
        404: ("NOT_FOUND", "请求的资源不存在"),
        405: ("METHOD_NOT_ALLOWED", "此接口不支持该请求方法"),
    }
    code, message = known.get(exc.status_code, ("HTTP_ERROR", HTTPStatus(exc.status_code).phrase))
    return error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    issues = [ValidationIssue(loc=list(issue["loc"]), type=issue["type"]) for issue in exc.errors()]
    return error_response(
        request,
        status_code=422,
        code="REQUEST_VALIDATION_ERROR",
        message="请求参数不符合接口要求",
        details=issues,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled request exception",
        extra={
            "event": "request_failed",
            "exception_type": type(exc).__name__,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    return error_response(
        request,
        status_code=500,
        code="INTERNAL_SERVER_ERROR",
        message="服务器内部错误，请使用请求 ID 联系维护者",
    )


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    return error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
