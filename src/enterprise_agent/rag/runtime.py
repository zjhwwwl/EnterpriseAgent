"""Bound real inference work until its thread actually finishes, even on timeout."""

import asyncio
import hashlib
import logging

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.rag.store import ChromaStore

logger = logging.getLogger("enterprise_agent.rag")


class RagRuntime:
    def __init__(self, settings: Settings, *, models=None, store=None):
        if models is None:
            from enterprise_agent.rag.backends import ModelRuntime

            models = ModelRuntime(settings)
        self.settings = settings
        self.models = models
        self.fingerprint = hashlib.sha256(
            f"{models.fingerprint}:parser-v1:{settings.rag_chunk_size}:{settings.rag_chunk_overlap}".encode()
        ).hexdigest()
        self.store = store or ChromaStore(settings, models.fingerprint)
        self._capacity = asyncio.Semaphore(1)
        self._tasks: set[asyncio.Task] = set()

    async def call(self, function, *args):
        try:
            async with asyncio.timeout(self.settings.rag_timeout_seconds):
                await self._capacity.acquire()
                task = asyncio.create_task(asyncio.to_thread(function, *args))
                self._tasks.add(task)
                task.add_done_callback(self._finished)
                return await asyncio.shield(task)
        except TimeoutError:
            raise BusinessError("rag_timeout", "文档推理或检索超时，请稍后重试", 503) from None

    def _finished(self, task):
        self._tasks.discard(task)
        self._capacity.release()
        if not task.cancelled():
            task.exception()  # Retrieve late failures without logging raw content.

    async def ready(self):
        if not await self.call(self.models.ready):
            return False
        return await self.call(self.store.ready)

    async def close(self):
        if self._tasks:
            logger.info("Waiting for local inference to finish", extra={"event": "rag_shutdown"})
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if hasattr(self.store, "close"):
            await asyncio.to_thread(self.store.close)
