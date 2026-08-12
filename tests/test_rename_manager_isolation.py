import asyncio
from types import SimpleNamespace

import pytest

import utils.rename_manager as rename_mod
from utils.rename_manager import _RenameManager


def _disable_pacing(monkeypatch) -> None:
    monkeypatch.setattr(rename_mod, "CHANNEL_RENAME_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(rename_mod, "CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL", 0)
    monkeypatch.setattr(rename_mod, "CHANNEL_RENAME_MIN_INTERVAL_GLOBAL", 0)


@pytest.mark.asyncio
async def test_blocked_channel_edit_does_not_block_another_channel(monkeypatch):
    """A long discord.py rate-limit wait must stay local to one channel."""

    _disable_pacing(monkeypatch)
    manager = _RenameManager()

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_finished = asyncio.Event()

    channels = {}
    guild = SimpleNamespace(get_channel=lambda cid: channels.get(cid))

    async def first_edit(*, name):
        assert name == "first-new"
        first_started.set()
        await release_first.wait()

    async def second_edit(*, name):
        assert name == "second-new"
        second_finished.set()

    first = SimpleNamespace(
        id=101,
        name="first-old",
        guild=guild,
        edit=first_edit,
    )
    second = SimpleNamespace(
        id=202,
        name="second-old",
        guild=guild,
        edit=second_edit,
    )
    channels.update({first.id: first, second.id: second})

    await manager.start()
    try:
        await manager.request(first, "first-new")
        await asyncio.wait_for(first_started.wait(), timeout=0.25)

        # The first edit deliberately remains blocked, modelling discord.py
        # waiting on a per-channel Retry-After value.
        await manager.request(second, "second-new")
        await asyncio.wait_for(second_finished.wait(), timeout=0.25)

        assert first_started.is_set()
        assert not release_first.is_set()
    finally:
        release_first.set()
        await asyncio.wait_for(manager._queue.join(), timeout=0.5)
        await manager.aclose()


@pytest.mark.asyncio
async def test_pending_names_for_blocked_channel_are_coalesced_locally(monkeypatch):
    """New names for one blocked channel must not create duplicate global work."""

    _disable_pacing(monkeypatch)
    manager = _RenameManager()

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = []

    channel = None

    def get_channel(cid):
        return channel if channel is not None and cid == channel.id else None

    guild = SimpleNamespace(get_channel=get_channel)

    async def edit(*, name):
        calls.append(name)
        if len(calls) == 1:
            first_started.set()
            await release_first.wait()

    channel = SimpleNamespace(
        id=303,
        name="old",
        guild=guild,
        edit=edit,
    )

    await manager.start()
    try:
        await manager.request(channel, "name-1")
        await asyncio.wait_for(first_started.wait(), timeout=0.25)

        await manager.request(channel, "name-2")
        await manager.request(channel, "name-3")

        release_first.set()
        await asyncio.wait_for(manager._queue.join(), timeout=0.5)

        # The latest pending value wins; the intermediate name is discarded.
        assert calls == ["name-1", "name-3"]
    finally:
        release_first.set()
        await manager.aclose()


@pytest.mark.asyncio
async def test_manager_429_backoff_is_local_to_affected_channel(monkeypatch):
    """Even manager-level 429 retry sleep must not block another channel."""

    _disable_pacing(monkeypatch)
    monkeypatch.setattr(rename_mod, "CHANNEL_RENAME_MAX_RETRIES", 1)

    class FakeHTTPException(Exception):
        def __init__(self, status):
            super().__init__(f"HTTP {status}")
            self.status = status

    monkeypatch.setattr(rename_mod.discord, "HTTPException", FakeHTTPException)

    original_sleep = asyncio.sleep
    backoff_started = asyncio.Event()
    release_backoff = asyncio.Event()
    second_finished = asyncio.Event()

    async def controlled_sleep(delay):
        task = asyncio.current_task()
        if delay == 1 and task is not None and task.get_name() == "rename-channel-404":
            backoff_started.set()
            await release_backoff.wait()
            return
        await original_sleep(0)

    monkeypatch.setattr(rename_mod.asyncio, "sleep", controlled_sleep)

    channels = {}
    guild = SimpleNamespace(get_channel=lambda cid: channels.get(cid))
    attempts = 0

    async def limited_edit(*, name):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FakeHTTPException(429)

    async def healthy_edit(*, name):
        second_finished.set()

    limited = SimpleNamespace(
        id=404,
        name="limited-old",
        guild=guild,
        edit=limited_edit,
    )
    healthy = SimpleNamespace(
        id=505,
        name="healthy-old",
        guild=guild,
        edit=healthy_edit,
    )
    channels.update({limited.id: limited, healthy.id: healthy})

    manager = _RenameManager()
    await manager.start()
    try:
        await manager.request(limited, "limited-new")
        await asyncio.wait_for(backoff_started.wait(), timeout=0.25)

        await manager.request(healthy, "healthy-new")
        await asyncio.wait_for(second_finished.wait(), timeout=0.25)

        assert attempts == 1
    finally:
        release_backoff.set()
        await asyncio.wait_for(manager._queue.join(), timeout=0.5)
        await manager.aclose()
