import asyncio
import logging

import pytest

from utils.background_tasks import BackgroundTaskRegistry


@pytest.mark.asyncio
async def test_registry_keeps_strong_reference_until_task_finishes():
    registry = BackgroundTaskRegistry()
    release = asyncio.Event()

    async def worker() -> None:
        await release.wait()

    task = registry.create_task(worker(), name="test:strong-reference")
    await asyncio.sleep(0)

    assert registry.pending_count == 1
    assert not task.done()

    release.set()
    await task
    await asyncio.sleep(0)

    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_registry_consumes_and_logs_task_exception(caplog):
    registry = BackgroundTaskRegistry()
    failed = asyncio.Event()

    async def worker() -> None:
        failed.set()
        raise RuntimeError("checkpoint exploded")

    with caplog.at_level(logging.ERROR, logger="utils.background_tasks"):
        registry.create_task(worker(), name="checkpoint:test-save")
        await asyncio.wait_for(failed.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert registry.pending_count == 0
    assert "background task failed: checkpoint:test-save" in caplog.text
    assert "checkpoint exploded" in caplog.text


@pytest.mark.asyncio
async def test_registry_aclose_cancels_and_awaits_pending_tasks():
    registry = BackgroundTaskRegistry()
    started = asyncio.Event()
    finalized = asyncio.Event()
    never = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await never.wait()
        finally:
            finalized.set()

    task = registry.create_task(worker(), name="test:shutdown")
    await asyncio.wait_for(started.wait(), timeout=1)

    await registry.aclose()

    assert task.cancelled()
    assert finalized.is_set()
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_registry_rejects_new_tasks_after_close():
    registry = BackgroundTaskRegistry()
    await registry.aclose()

    async def worker() -> None:
        return None

    with pytest.raises(RuntimeError, match="registry is closed"):
        registry.create_task(worker(), name="test:too-late")
