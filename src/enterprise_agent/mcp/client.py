"""Official SDK client example for the local, authenticated stdio server."""

import argparse
import asyncio
import json
import re
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import SecretStr, ValidationError

from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.logging_config import redact_sensitive
from enterprise_agent.mcp.server import TOOL_NAMES
from enterprise_agent.tools.business import OrderInput

CLIENT_TIMEOUT_SECONDS = 60
DEFAULT_API_URL = "http://127.0.0.1:8000"


def loopback_origin(value: str) -> str:
    """Credentials may only be sent to a plain local HTTP origin."""
    try:
        url = urlsplit(value)
        if (
            url.scheme != "http"
            or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username is not None
            or url.password is not None
            or url.path
            or url.query
            or url.fragment
            or "?" in value
            or "#" in value
            or any(character.isspace() for character in value)
            or re.fullmatch(
                r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?", url.netloc, re.IGNORECASE
            )
            is None
            or url.port == 0
        ):
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("API URL must be a plain local HTTP origin") from None
    return value


def order_number(value: str) -> str:
    try:
        return OrderInput(order_no=value).order_no
    except ValidationError:
        raise argparse.ArgumentTypeError("Order number must contain 5 to 30 digits") from None


def child_environment(settings: Settings, token: SecretStr) -> dict[str, str]:
    """Pass only the ordinary database, user credential and local RAG settings."""
    if settings.database_url is None or settings.jwt_secret is None:
        raise BusinessError("mcp_configuration_missing", "MCP 业务数据库和认证尚未配置", 503)
    temporary = PROJECT_ROOT / "var" / "tmp" / "mcp"
    temporary.mkdir(parents=True, exist_ok=True)
    field_names = (
        "app_env",
        "data_dir",
        "log_level",
        "mcp_request_timeout_seconds",
        "rag_model_dir",
        "rag_model_threads",
        "chroma_host",
        "chroma_port",
        "chroma_timeout_seconds",
        "rag_collection_prefix",
        "rag_chunk_size",
        "rag_chunk_overlap",
        "rag_timeout_seconds",
        "rag_max_chunks",
        "rag_min_vector_similarity",
        "rag_min_rerank_score",
    )
    result = {"EA_" + name.upper(): str(getattr(settings, name)) for name in field_names}
    result.update(
        {
            "EA_DATABASE_URL": settings.database_url.get_secret_value(),
            "EA_JWT_SECRET": settings.jwt_secret.get_secret_value(),
            "EA_MCP_ACCESS_TOKEN": token.get_secret_value(),
            "EA_RAG_ENABLED": str(settings.rag_enabled).lower(),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "PYTHONUTF8": "1",
        }
    )
    return result


async def _demo_login(settings: Settings, api_url: str) -> SecretStr:
    endpoint = loopback_origin(api_url) + "/api/v1/auth/login"
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as http:
        response = await http.post(
            endpoint,
            json={
                "username": "demo_customer",
                "password": settings.demo_password.get_secret_value(),
            },
        )
        if response.status_code != 200:
            raise BusinessError("mcp_demo_login_failed", "本地模拟用户登录失败", 401)
        try:
            token = response.json()["access_token"]
            if not isinstance(token, str) or not token or len(token) > 8192 or token.isspace():
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise BusinessError("mcp_demo_login_failed", "本地登录响应不符合要求", 503) from None
        return SecretStr(token)


@asynccontextmanager
async def _stdio_session(parameters: StdioServerParameters):
    async with (
        stdio_client(parameters, errlog=sys.stderr) as (reader, writer),
        ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=CLIENT_TIMEOUT_SECONDS)
        ) as session,
    ):
        yield session


async def run_demo(
    settings: Settings,
    *,
    order_no: str = "10086",
    demo_login: bool = False,
    api_url: str = DEFAULT_API_URL,
    session_factory=None,
) -> dict:
    order_no = order_number(order_no)
    api_url = loopback_origin(api_url)
    token = await _demo_login(settings, api_url) if demo_login else settings.mcp_access_token
    if token is None:
        raise BusinessError(
            "mcp_configuration_missing", "请配置 MCP 用户凭证或使用本地模拟登录", 503
        )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "enterprise_agent.mcp.server"],
        cwd=PROJECT_ROOT,
        env=child_environment(settings, token),
    )
    factory = session_factory or _stdio_session
    async with asyncio.timeout(CLIENT_TIMEOUT_SECONDS), factory(parameters) as session:
        initialized = await session.initialize()
        discovered = await session.list_tools()
        available = {tool.name for tool in discovered.tools}
        if not set(TOOL_NAMES) <= available:
            raise BusinessError("mcp_tool_discovery_failed", "MCP 服务缺少预期业务工具", 503)
        results = []
        for name in TOOL_NAMES:
            arguments = {"order_no": order_no}
            if name == "query_refund_policy":
                arguments["reason"] = "change_mind"
            result = await session.call_tool(name, arguments)
            structured = result.structuredContent
            if (
                not isinstance(structured, dict)
                or ("error" if result.isError else "data") not in structured
            ):
                raise BusinessError("mcp_response_invalid", "MCP 工具响应不符合要求", 503)
            key = "error" if result.isError else "data"
            results.append({"tool": name, "is_error": bool(result.isError), key: structured[key]})
        report = {
            "transport": "stdio",
            "server_name": initialized.serverInfo.name,
            "server_version": initialized.serverInfo.version,
            "order_no": order_no,
            "tools_discovered": sorted(available),
            "success": not any(result["is_error"] for result in results),
            "results": results,
            "provider_calls": 0,
            "token_usage": None,
        }
    return redact_sensitive(
        report, secret_values=(*settings.secret_values(), token.get_secret_value())
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--demo-login", action="store_true")
    parser.add_argument("--api-url", type=loopback_origin, default=DEFAULT_API_URL)
    parser.add_argument("--order-no", type=order_number, default="10086")
    parser.add_argument("--output", type=Path)
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        parser.error("Unsupported arguments")
    if args.output is not None and args.output.suffix.lower() != ".json":
        parser.error("Output path must name a JSON report")
    try:
        settings = Settings(
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
        # Windows subprocess pipes require a Proactor loop. The API/PG server
        # uses Selector in its separate process, without changing this client.
        loop_factory = asyncio.ProactorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            report = runner.run(
                run_demo(
                    settings,
                    order_no=args.order_no,
                    demo_login=args.demo_login,
                    api_url=args.api_url,
                )
            )
        serialized = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        return 0 if report["success"] else 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(
            f"MCP client failed ({type(exc).__name__}); check local configuration and readiness",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
