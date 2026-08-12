import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.radio import RadioCog
from config import RADIO_RAP_STREAM_URL, RADIO_STREAM_URL, RADIO_VC_ID


class FakeResponse:
    def __init__(self) -> None:
        self._done = False
        self.send_message = AsyncMock()
        self.defer = AsyncMock(side_effect=self._mark_done)

    def _mark_done(self, *args, **kwargs):
        self._done = True

    def is_done(self) -> bool:
        return self._done


@pytest.mark.asyncio
async def test_radio_rap_toggles_stream_without_renaming_channel():
    channel = SimpleNamespace(id=RADIO_VC_ID, name="📻・Radio", edit=AsyncMock())
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), get_channel=lambda cid: channel)
    cog = RadioCog(bot)
    cog._connect_and_play = AsyncMock()

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=FakeResponse(),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.radio_rap(interaction)

    assert cog.stream_url == RADIO_RAP_STREAM_URL
    assert cog._previous_stream == RADIO_STREAM_URL
    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()

    interaction.response = FakeResponse()
    interaction.followup.send.reset_mock()

    await cog.radio_rap(interaction)

    assert cog.stream_url == RADIO_STREAM_URL
    assert cog._previous_stream is None
    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
