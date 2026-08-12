import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.radio import RadioCog
from config import RADIO_RAP_STREAM_URL, RADIO_STREAM_URL, RADIO_VC_ID


@pytest.mark.asyncio
async def test_radio_hiphop_restores_default_without_renaming_channel():
    channel = SimpleNamespace(id=RADIO_VC_ID, name="📻・Radio", edit=AsyncMock())
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), get_channel=lambda cid: channel)
    cog = RadioCog(bot)
    cog.stream_url = RADIO_RAP_STREAM_URL
    cog.voice = SimpleNamespace(is_playing=lambda: True, stop=lambda: None)
    cog._connect_and_play = AsyncMock()

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await cog.radio_hiphop(interaction)

    assert cog.stream_url == RADIO_STREAM_URL
    assert cog._previous_stream is None
    assert channel.name == "📻・Radio"
    channel.edit.assert_not_awaited()
    cog._connect_and_play.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()
