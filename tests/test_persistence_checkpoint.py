import asyncio

import pytest

from utils import persistence


@pytest.fixture(autouse=True)
def reset_checkpoint_state(monkeypatch):
    monkeypatch.setattr(persistence, "_checkpoint_tasks", {})
    monkeypatch.setattr(persistence, "_checkpoint_lock", asyncio.Lock())


@pytest.mark.asyncio
async def test_schedule_checkpoint_deduplicates_same_save_function():
    calls = 0
    completed = asyncio.Event()

    async def save() -> None:
        nonlocal calls
        calls += 1
        completed.set()

    await persistence.schedule_checkpoint(save, delay=0)
    await persistence.schedule_checkpoint(save, delay=0)

    await asyncio.wait_for(completed.wait(), timeout=1)
    await asyncio.sleep(0)

    assert calls == 1


@pytest.mark.asyncio
async def test_schedule_checkpoint_keeps_independent_save_functions():
    completed_a = asyncio.Event()
    completed_b = asyncio.Event()

    async def save_a() -> None:
        completed_a.set()

    async def save_b() -> None:
        completed_b.set()

    await persistence.schedule_checkpoint(save_a, delay=0)
    await persistence.schedule_checkpoint(save_b, delay=0)

    await asyncio.wait_for(
        asyncio.gather(completed_a.wait(), completed_b.wait()),
        timeout=1,
    )
    await asyncio.sleep(0)

    assert completed_a.is_set()
    assert completed_b.is_set()


@pytest.mark.asyncio
async def test_schedule_checkpoint_can_reschedule_after_completion():
    calls = 0
    first_completed = asyncio.Event()
    second_completed = asyncio.Event()

    async def save() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_completed.set()
        elif calls == 2:
            second_completed.set()

    await persistence.schedule_checkpoint(save, delay=0)
    await asyncio.wait_for(first_completed.wait(), timeout=1)
    await asyncio.sleep(0)

    await persistence.schedule_checkpoint(save, delay=0)
    await asyncio.wait_for(second_completed.wait(), timeout=1)
    await asyncio.sleep(0)

    assert calls == 2
