import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.radio import RadioCog
from config import ROCK_RADIO_STREAM_URL, RADIO_STREAM_URL, RADIO_VC_ID


@pytest.mark.asyncio
async def test_radio_rock_toggles_stream_without_renaming_channel():
    channel = SimpleNamespace(id=RADIO_VC_ID, name="📻・Radio", edit=AsyncMock())
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), get_channel=lambda cid: channel)
    cog = RadioCog(bot)
    monkey_connect = AsyncMock()
    cog._connect_and_play = monkey_connect

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await cog.radio_rock(interaction)

    assert cog.stream_url == ROCK_RADIO_STREAM_URL
    assert cog._previous_stream == RADIO_STREAM_URL
    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()

    interaction.response.send_message.reset_mock()

    await cog.radio_rock(interaction)

    assert cog.stream_url == RADIO_STREAM_URL
    assert cog._previous_stream is None
    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
