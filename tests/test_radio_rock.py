import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
import cogs.radio as radio_mod
from cogs.radio import RadioCog
from config import ROCK_RADIO_STREAM_URL, RADIO_STREAM_URL, RADIO_VC_ID


class FakeResponse:
    def __init__(self) -> None:
        self._done = False
        self.defer = AsyncMock(side_effect=self._mark_done)
        self.send_message = AsyncMock()

    def _mark_done(self, *args, **kwargs) -> None:
        self._done = True

    def is_done(self) -> bool:
        return self._done


def test_radio_module_no_longer_depends_on_rename_manager():
    assert not hasattr(radio_mod, "rename_manager")


@pytest.mark.asyncio
async def test_legacy_rename_helper_is_a_noop():
    channel = SimpleNamespace(id=RADIO_VC_ID, name="📻・Radio", edit=AsyncMock())
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), get_channel=lambda cid: channel)
    cog = RadioCog(bot)

    await cog._rename_for_stream(channel, ROCK_RADIO_STREAM_URL)

    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_radio_rock_toggles_stream_without_renaming_channel():
    channel = SimpleNamespace(id=RADIO_VC_ID, name="📻・Radio", edit=AsyncMock())
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), get_channel=lambda cid: channel)
    cog = RadioCog(bot)
    cog._connect_and_play = AsyncMock()

    response = FakeResponse()
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=response,
        followup=followup,
    )

    await cog.radio_rock(interaction)

    assert cog.stream_url == ROCK_RADIO_STREAM_URL
    assert cog._previous_stream == RADIO_STREAM_URL
    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()
    response.defer.assert_awaited_once_with(ephemeral=True)
    followup.send.assert_awaited_once()
    response.send_message.assert_not_awaited()

    followup.send.reset_mock()

    await cog.radio_rock(interaction)

    assert cog.stream_url == RADIO_STREAM_URL
    assert cog._previous_stream is None
    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()
    response.defer.assert_awaited_once()
    followup.send.assert_awaited_once()
    response.send_message.assert_not_awaited()
