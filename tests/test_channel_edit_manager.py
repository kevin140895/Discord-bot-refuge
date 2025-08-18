from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
import pytest_asyncio

import utils.channel_edit_manager as cem
import utils.discord_utils as du
from utils.discord_utils import safe_channel_edit


@pytest_asyncio.fixture
async def edit_manager(monkeypatch):
    mgr = cem._ChannelEditManager()
    monkeypatch.setattr(cem, "channel_edit_manager", mgr)
    monkeypatch.setattr(du, "channel_edit_manager", mgr)
    await mgr.start()
    try:
        yield mgr
    finally:
        mgr.stop()


@pytest.mark.asyncio
async def test_channel_edit_manager_respects_per_channel_interval(monkeypatch, edit_manager):
    channel = SimpleNamespace(id=1, topic="Old", edit=AsyncMock())
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(cem.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cem, "CHANNEL_EDIT_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(cem, "CHANNEL_EDIT_MIN_INTERVAL_SECONDS", 2)
    monkeypatch.setattr(cem, "CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS", 0)

    await safe_channel_edit(channel, topic="First")
    await edit_manager._queue.join()
    await safe_channel_edit(channel, topic="Second")
    await edit_manager._queue.join()

    assert delays == [pytest.approx(2, abs=0.1)]
    channel.edit.assert_has_awaits([call(topic="First"), call(topic="Second")])


@pytest.mark.asyncio
async def test_channel_edit_manager_respects_global_interval(monkeypatch, edit_manager):
    ch1 = SimpleNamespace(id=1, name="A", edit=AsyncMock())
    ch2 = SimpleNamespace(id=2, name="B", edit=AsyncMock())
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(cem.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cem, "CHANNEL_EDIT_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(cem, "CHANNEL_EDIT_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(cem, "CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS", 3)

    await safe_channel_edit(ch1, name="A1")
    await safe_channel_edit(ch2, name="B1")
    await edit_manager._queue.join()

    assert delays == [pytest.approx(3, abs=0.1)]
    ch1.edit.assert_awaited_once()
    ch2.edit.assert_awaited_once()
