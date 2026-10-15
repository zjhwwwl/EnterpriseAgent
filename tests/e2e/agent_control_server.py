"""Loopback-only subprocess driver for the real Agent restart integration test.

ScriptedTestDriver chooses two known actions. It is not a DeepSeek response,
does not measure model quality and never requests or reads a provider API key.
All authentication, business rules, PostgreSQL checkpoints and knowledge
retrieval use the application implementation and actual local services.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import uvicorn
from database_scope import require_test_database
from sqlalchemy.engine import make_url

from enterprise_agent.agents.llm import ChatReply, ModelCallError, ModelToolCall
from enterprise_agent.application import create_app
from enterprise_agent.config import PROJECT_ROOT, Settings


class ScriptedTestDriver:
    """A bounded test controller; zero provider tokens are consumed."""

    async def complete(self, messages, *, tools=None):
        names = {item["function"]["name"] for item in tools or []}
        if names == {"route_task"}:
            name, arguments = (
                "route_task",
                {
                    "intent": "order",
                    "query": "为订单10086创建改变主意退货申请",
                },
            )
        elif "create_refund_request" in names:
            name, arguments = (
                "create_refund_request",
                {
                    "order_no": "10086",
                    "reason": "change_mind",
                    "notes": "真实TCP重启控制测试；不接入真实支付",
                },
            )
        else:
            raise ModelCallError("agent_model_invalid_response", "Unexpected test-driver stage")
        identifier = "call_control_server_" + uuid4().hex
        return ChatReply(
            "",
            [ModelToolCall(identifier, name, arguments)],
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": identifier,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            },
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            0,
        )


def main():
    if Path(__file__).resolve().parents[2] != PROJECT_ROOT.resolve():
        raise SystemExit("The controlled server must run from this project's tests directory")
    settings = Settings(_env_file=None)
    if settings.database_url is None:
        raise SystemExit("An explicitly configured dedicated test database is required")
    database = make_url(settings.database_url.get_secret_value())
    try:
        require_test_database(database)
    except ValueError:
        raise SystemExit(
            "The controlled server refuses a database outside its test scope"
        ) from None
    if (
        settings.app_env != "test"
        or str(settings.host) != "127.0.0.1"
        or not settings.agent_enabled
        or not settings.rag_enabled
        or settings.llm_enabled
        or settings.deepseek_api_key is not None
        or settings.migration_database_url is not None
        or settings.test_migration_database_url is not None
        or not settings.rag_collection_prefix.startswith("ea_e2e_")
    ):
        raise SystemExit("The controlled server refuses unsafe runtime configuration")
    # Extra inherited provider/tracing variables must not enable an external call.
    if any(key.startswith(("LANGSMITH", "LANGCHAIN", "OPENAI", "DEEPSEEK")) for key in os.environ):
        raise SystemExit("The controlled server refuses inherited provider/tracing configuration")
    app = create_app(settings, agent_model=ScriptedTestDriver())
    app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=UTC)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=settings.port,
            access_log=False,
        )
    )
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
