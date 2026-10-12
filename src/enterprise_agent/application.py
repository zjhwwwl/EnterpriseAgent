"""Side-effect-free application factory, separate from the ASGI entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from enterprise_agent import DEVELOPMENT_PHASE, __version__
from enterprise_agent.api.errors import register_error_handlers
from enterprise_agent.api.routes.system import router
from enterprise_agent.config import Settings, get_settings
from enterprise_agent.logging_config import configure_logging
from enterprise_agent.request_context import RequestContextMiddleware

logger = logging.getLogger("enterprise_agent.lifecycle")


def create_app(settings: Settings | None = None, *, configure_logs: bool = True) -> FastAPI:
    resolved = settings if settings is not None else get_settings()
    if configure_logs:
        configure_logging(resolved.log_level, secret_values=resolved.secret_values())

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.ready = True
        logger.info(
            "Application started",
            extra={"event": "application_started", "phase": DEVELOPMENT_PHASE},
        )
        try:
            yield
        finally:
            application.state.ready = False
            logger.info("Application stopped", extra={"event": "application_stopped"})

    application = FastAPI(
        title="EnterpriseAgent",
        description="Phase 1：应用骨架与健康接口；电商、RAG、Agent 功能按阶段开发。",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
    )
    application.state.settings = resolved
    application.state.ready = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    application.add_middleware(RequestContextMiddleware)
    register_error_handlers(application)
    application.include_router(router)
    return application
