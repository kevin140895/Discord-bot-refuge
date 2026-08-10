import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import cogs.rock_radio as rock_radio


class _FakeFuture:
    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


def test_after_play_returns_to_bot_loop_from_audio_thread(monkeypatch):
    bot_loop = object()
    cog = rock_radio.RockRadioCog(SimpleNamespace(loop=bot_loop))
    scheduled = _FakeFuture()
    calls = []

    def fake_run_coroutine_threadsafe(coro, loop):
        calls.append(loop)
        coro.close()
        return scheduled

    monkeypatch.setattr(
        rock_radio.asyncio,
        "run_coroutine_threadsafe",
        fake_run_coroutine_threadsafe,
    )

    cog._after_play(None)

    assert calls == [bot_loop]
    assert cog._reconnect_task is scheduled


@pytest.mark.asyncio
async def test_connect_failure_does_not_call_play_stream(monkeypatch):
    cog = rock_radio.RockRadioCog(SimpleNamespace())
    cog.stream_url = "https://radio.invalid/rock"
    play_stream = Mock()

    monkeypatch.setattr(
        rock_radio,
        "ensure_voice",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(rock_radio, "play_stream", play_stream)

    await cog._connect_and_play()

    play_stream.assert_not_called()
    assert cog._reconnect_task is not None
    cog._reconnect_task.cancel()
    with suppress(asyncio.CancelledError):
        await cog._reconnect_task


@pytest.mark.asyncio
async def test_disconnected_voice_is_not_used_for_playback(monkeypatch):
    cog = rock_radio.RockRadioCog(SimpleNamespace())
    cog.stream_url = "https://radio.invalid/rock"
    disconnected_voice = SimpleNamespace(is_connected=lambda: False)
    play_stream = Mock()

    monkeypatch.setattr(
        rock_radio,
        "ensure_voice",
        AsyncMock(return_value=disconnected_voice),
    )
    monkeypatch.setattr(rock_radio, "play_stream", play_stream)

    await cog._connect_and_play()

    play_stream.assert_not_called()
    assert cog._reconnect_task is not None
    cog._reconnect_task.cancel()
    with suppress(asyncio.CancelledError):
        await cog._reconnect_task


@pytest.mark.asyncio
async def test_failed_delayed_reconnect_can_schedule_next_attempt(monkeypatch):
    cog = rock_radio.RockRadioCog(SimpleNamespace())
    cog.stream_url = "https://radio.invalid/rock"
    cog._reconnect_task = _FakeFuture()
    scheduled = []

    async def no_sleep(_delay):
        return None

    def fake_create_task(coro):
        coro.close()
        future = _FakeFuture()
        scheduled.append(future)
        return future

    monkeypatch.setattr(rock_radio.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(rock_radio.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(
        rock_radio,
        "ensure_voice",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(rock_radio, "play_stream", Mock())

    await cog._delayed_reconnect()

    assert len(scheduled) == 1
    assert cog._reconnect_task is scheduled[0]
