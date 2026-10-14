"""Read-only, single-user stdio MCP server; stdout is reserved for JSON-RPC."""

import asyncio
import json
import logging
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from enterprise_agent import __version__
from enterprise_agent.config import Settings
from enterprise_agent.database import Database
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.logging_config import configure_logging
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.services.auth import AuthService
from enterprise_agent.tools.business import BusinessTools, OrderInput, RefundPolicyInput

logger = logging.getLogger("enterprise_agent.mcp")

_TOOLS = {
    "query_order": ("get_order", OrderInput, "查询当前用户有权访问的订单；金额单位为人民币分。"),
    "query_logistics": ("get_logistics", OrderInput, "查询有权访问订单的物流状态和已有物流事件。"),
    "query_refund_policy": (
        "get_refund_policy",
        RefundPolicyInput,
        "结合订单事实、付款时适用政策和知识文档来源查询退款条件，不创建退款申请。",
    ),
}
TOOL_NAMES = tuple(_TOOLS)
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"data": {"type": ["object", "array"]}},
    "required": ["data"],
    "additionalProperties": False,
}


def _result(payload: dict[str, Any], *, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=json.dumps(payload, ensure_ascii=False, allow_nan=False)
            )
        ],
        structuredContent=payload,
        isError=error,
    )


def _error(error: BusinessError) -> types.CallToolResult:
    return _result(
        {
            "error": {
                "code": error.code,
                "message": error.message,
                "status_code": error.status_code,
            }
        },
        error=True,
    )


def create_server(*, tools: BusinessTools, auth: AuthService, settings: Settings) -> Server:
    """Injectable protocol boundary; identity is resolved afresh for every call."""
    server = Server("EnterpriseAgent", version=__version__)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=description,
                inputSchema=model.model_json_schema(),
                outputSchema=_OUTPUT_SCHEMA,
                annotations=types.ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
            for name, (_, model, description) in _TOOLS.items()
        ]

    # Pydantic is the shared strict validator. SDK JSON-schema validation errors
    # include the rejected user value, so they must not reach the public channel.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        started = time.monotonic()
        safe_name = name if name in _TOOLS else "unknown"
        outcome = "success"
        error_code = None
        try:
            async with asyncio.timeout(settings.mcp_request_timeout_seconds):
                if settings.mcp_access_token is None:
                    raise BusinessError("authentication_unavailable", "MCP 身份凭证尚未配置", 503)
                actor = await auth.authenticate(settings.mcp_access_token.get_secret_value())
                if name not in _TOOLS:
                    raise BusinessError("unknown_tool", "请求的 MCP 工具不存在", 422)
                internal_name = _TOOLS[name][0]
                payload = BusinessTools.validate_arguments(internal_name, arguments)
                value = await tools.execute(actor, internal_name, payload)
                result = _result({"data": value})
        except BusinessError as exc:
            outcome, error_code = "error", exc.code
            result = _error(exc)
        except TimeoutError:
            outcome, error_code = "error", "mcp_request_timeout"
            result = _error(BusinessError(error_code, "业务查询超时，请稍后重试", 503))
        except Exception as exc:
            outcome, error_code = "error", "mcp_tool_unavailable"
            logger.warning(
                "MCP business query failed",
                extra={"event": "mcp_tool_failure", "exception_type": type(exc).__name__},
            )
            result = _error(BusinessError(error_code, "业务查询暂不可用，请稍后重试", 503))
        logger.info(
            "MCP tool finished",
            extra={
                "event": "mcp_tool_finished",
                "tool_name": safe_name,
                "outcome": outcome,
                "error_code": error_code,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return result

    return server


@asynccontextmanager
async def _runtime(settings: Settings):
    if settings.database_url is None or settings.jwt_secret is None:
        raise BusinessError("mcp_configuration_missing", "MCP 业务数据库和认证尚未配置", 503)
    if settings.mcp_access_token is None:
        raise BusinessError("mcp_configuration_missing", "MCP 身份凭证尚未配置", 503)
    async with AsyncExitStack() as stack:
        database = Database(settings.database_url.get_secret_value())
        stack.push_async_callback(database.close)
        if not await database.is_ready():
            raise BusinessError("database_not_ready", "业务数据库尚未就绪", 503)
        rag = None
        if settings.rag_enabled:
            rag = RagRuntime(settings)
            stack.push_async_callback(rag.close)
            if not await rag.ready():
                raise BusinessError("rag_not_ready", "知识检索尚未就绪", 503)
        yield create_server(
            tools=BusinessTools(database.session_factory, settings, rag),
            auth=AuthService(database.session_factory, settings),
            settings=settings,
        )


def _configure_protocol_logging(settings: Settings) -> None:
    configure_logging(settings.log_level, secret_values=settings.secret_values(), stream=sys.stderr)
    # The SDK logs entire incoming requests at DEBUG and unknown tool names at
    # WARNING. Keep those values out of logs, even when EA_LOG_LEVEL=DEBUG.
    protocol_logger = logging.getLogger("mcp")
    protocol_logger.handlers = [logging.NullHandler()]
    protocol_logger.setLevel(logging.CRITICAL)
    protocol_logger.propagate = False
    # Malformed request envelopes are logged through the SDK's root logger with
    # Pydantic's raw input values. This dedicated process emits only the explicit
    # enterprise logger above; SDK diagnostics must not leak user parameters.
    root_logger = logging.getLogger()
    root_logger.handlers = [logging.NullHandler()]
    root_logger.setLevel(logging.CRITICAL)


async def run(settings: Settings | None = None) -> None:
    configured = settings or Settings(
        _env_file=None,
        llm_enabled=False,
        deepseek_api_key=None,
        agent_enabled=False,
        migration_database_url=None,
        test_database_url=None,
        test_migration_database_url=None,
        redis_url=None,
        analytics_enabled=False,
        analytics_database_url=None,
        test_analytics_database_url=None,
    )
    _configure_protocol_logging(configured)
    async with _runtime(configured) as server, stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> int:
    """Never print settings, credential values or raw startup exceptions."""
    configure_logging(stream=sys.stderr)
    try:
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(run())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error(
            "MCP server could not run; check local configuration and readiness",
            extra={"event": "mcp_server_failure", "exception_type": type(exc).__name__},
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
