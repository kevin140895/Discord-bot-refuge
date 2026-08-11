from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.radio as radio_module


def _suspended_radio() -> radio_module.RadioCog:
    cog = radio_module.RadioCog.__new__(radio_module.RadioCog)
    cog.bot = SimpleNamespace(loop=object(), user=SimpleNamespace(id=999))
    cog.stream_url = None
    cog._reconnect_task = None
    cog._connect_and_play = AsyncMock()
    return cog


def test_after_play_does_not_reconnect_while_music2_suspends_radio(monkeypatch):
    cog = _suspended_radio()
    scheduled = []

    def fake_run_coroutine_threadsafe(coro, loop):
        scheduled.append((coro, loop))
        return SimpleNamespace(done=lambda: False)

    monkeypatch.setattr(
        radio_module.asyncio,
        "run_coroutine_threadsafe",
        fake_run_coroutine_threadsafe,
    )

    cog._after_play(None)

    assert scheduled == []
    assert cog._reconnect_task is None


@pytest.mark.asyncio
async def test_delayed_reconnect_rechecks_suspension_after_delay(monkeypatch):
    cog = _suspended_radio()
    sleep = AsyncMock()
    monkeypatch.setattr(radio_module.asyncio, "sleep", sleep)

    await cog._delayed_reconnect()

    sleep.assert_awaited_once_with(5)
    cog._connect_and_play.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_disconnect_does_not_schedule_reconnect_while_suspended(monkeypatch):
    cog = _suspended_radio()
    created = []

    def fake_create_task(coro):
        created.append(coro)
        return SimpleNamespace(done=lambda: False)

    monkeypatch.setattr(radio_module.asyncio, "create_task", fake_create_task)
    member = SimpleNamespace(id=999)
    before = SimpleNamespace(channel=object())
    after = SimpleNamespace(channel=None)

    await cog.on_voice_state_update(member, before, after)

    assert created == []
    assert cog._reconnect_task is None
