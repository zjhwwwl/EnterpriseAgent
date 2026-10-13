"""Side-effect-free application factory, separate from the ASGI entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from enterprise_agent import DEVELOPMENT_PHASE, __version__
from enterprise_agent.api.errors import register_error_handlers
from enterprise_agent.api.routes import agent, auth, knowledge, orders, refunds, system
from enterprise_agent.api.upload_limit import UploadLimitMiddleware
from enterprise_agent.config import Settings, get_settings
from enterprise_agent.database import Database
from enterprise_agent.logging_config import configure_logging
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.request_context import RequestContextMiddleware

logger = logging.getLogger("enterprise_agent.lifecycle")


def create_app(
    settings: Settings | None = None,
    *,
    configure_logs: bool = True,
    rag_runtime: RagRuntime | None = None,
    agent_model=None,
    agent_checkpoint=None,
) -> FastAPI:
    resolved = settings if settings is not None else get_settings()
    if configure_logs:
        configure_logging(resolved.log_level, secret_values=resolved.secret_values())

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            if resolved.database_url is not None:
                application.state.database = Database(resolved.database_url.get_secret_value())
                await application.state.database.is_ready()
            if resolved.redis_url is not None:
                application.state.redis = Redis.from_url(
                    resolved.redis_url.get_secret_value(),
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
            if resolved.rag_enabled:
                application.state.rag = (
                    rag_runtime if rag_runtime is not None else RagRuntime(resolved)
                )
            if (
                resolved.agent_enabled
                and application.state.database is not None
                and application.state.database.verified
            ):
                from enterprise_agent.agents.checkpoint import AgentCheckpointRuntime

                application.state.agent_checkpoint = agent_checkpoint or AgentCheckpointRuntime(
                    resolved.database_url.get_secret_value()
                )
                await application.state.agent_checkpoint.open()
                application.state.agent_model = agent_model
                if agent_model is None and resolved.llm_enabled:
                    from enterprise_agent.agents.llm import DeepSeekChatModel

                    application.state.agent_model = DeepSeekChatModel(resolved)
            application.state.ready = True
            logger.info(
                "Application started",
                extra={"event": "application_started", "phase": DEVELOPMENT_PHASE},
            )
            yield
        finally:
            application.state.ready = False
            if application.state.agent_model is not None and agent_model is None:
                await application.state.agent_model.aclose()
            if application.state.agent_checkpoint is not None and agent_checkpoint is None:
                await application.state.agent_checkpoint.close()
            if application.state.database is not None:
                await application.state.database.close()
            if application.state.redis is not None:
                await application.state.redis.aclose()
            if application.state.rag is not None and rag_runtime is None:
                await application.state.rag.close()
            logger.info("Application stopped", extra={"event": "application_stopped"})

    application = FastAPI(
        title="EnterpriseAgent",
        description="Phase 5：企业知识与订单 Agent、人工退款确认；另提供独立 stdio MCP 服务。",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
    )
    application.state.settings = resolved
    application.state.ready = False
    application.state.database = None
    application.state.redis = None
    application.state.rag = None
    application.state.agent_checkpoint = None
    application.state.agent_model = None
    application.state.clock = lambda: datetime.now(UTC)
    # add_middleware prepends each layer: RequestContext -> CORS -> UploadLimit.
    # CORS must also wrap UploadLimit's early responses before routing.
    application.add_middleware(UploadLimitMiddleware, max_bytes=resolved.rag_upload_max_bytes)
    application.add_middleware(
        UploadLimitMiddleware,
        max_bytes=16 * 1024,
        allowance=0,
        path_prefix="/api/v1/agent/",
        error_code="agent_request_too_large",
        message="Agent 请求超过正文大小限制",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )
    application.add_middleware(RequestContextMiddleware)
    register_error_handlers(application)
    for module in (system, auth, orders, refunds, knowledge, agent):
        application.include_router(module.router)
    return application
