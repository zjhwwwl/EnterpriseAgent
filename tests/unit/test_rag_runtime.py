"""Real blocking threads verify cancellation capacity; these are not model tests."""

import asyncio
import threading

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.rag.runtime import RagRuntime


class TestMetadata:
    fingerprint = "test-capacity-metadata"


@pytest.mark.parametrize("termination", ["timeout", "cancellation"])
async def test_capacity_is_held_until_the_blocking_thread_really_finishes(termination):
    # A short test-only deadline avoids sleeping for the production minimum.
    configured = Settings(_env_file=None).model_copy(update={"rag_timeout_seconds": 0.1})
    runtime = RagRuntime(configured, models=TestMetadata(), store=object())
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def blocking_operation():
        entered.set()
        assert release.wait(timeout=5)

    first = asyncio.create_task(runtime.call(blocking_operation))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if termination == "cancellation":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            with pytest.raises(BusinessError) as caught:
                await first
            assert caught.value.code == "rag_timeout"
        with pytest.raises(BusinessError):
            await runtime.call(second_entered.set)
        assert not second_entered.is_set()
    finally:
        release.set()
        await runtime.close()
    await runtime.call(second_entered.set)
    assert second_entered.is_set()
