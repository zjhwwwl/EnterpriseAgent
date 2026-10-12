"""Start the local server without logging raw access URLs."""

import uvicorn

from enterprise_agent.config import get_settings
from enterprise_agent.main import app


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=str(settings.host), port=settings.port, log_config=None, access_log=False)
