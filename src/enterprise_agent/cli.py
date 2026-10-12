"""Start the local server without logging raw access URLs."""

import asyncio
import sys

import uvicorn

from enterprise_agent.config import get_settings
from enterprise_agent.main import app


def main() -> None:
    settings = get_settings()
    config = uvicorn.Config(
        app, host=str(settings.host), port=settings.port, log_config=None, access_log=False
    )
    # Psycopg's async transport on Windows requires a SelectorEventLoop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(uvicorn.Server(config).serve())
