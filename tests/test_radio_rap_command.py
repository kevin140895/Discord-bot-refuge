import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
import cogs.radio as radio_mod
from cogs.radio import RadioCog
from config import RADIO_RAP_STREAM_URL, RADIO_STREAM_URL, RADIO_VC_ID


@pytest.mark.asyncio
async def test_radio_rap_command_toggles_stream(monkeypatch):
    class FakeVoiceChannel(SimpleNamespace):
        pass

    monkeypatch.setattr(radio_mod.discord, "VoiceChannel", FakeVoiceChannel)
    channel = FakeVoiceChannel(id=RADIO_VC_ID, name="Radio")
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), get_channel=lambda cid: channel)
    cog = RadioCog(bot)
    cog._original_name = "Radio"
    monkeypatch.setattr(cog, "_connect_and_play", AsyncMock())
    rename_mock = AsyncMock()
    monkeypatch.setattr(radio_mod.rename_manager, "request", rename_mock)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    await RadioCog.radio_rap.callback(cog, interaction)

    assert cog.stream_url == RADIO_RAP_STREAM_URL
    assert cog._previous_stream == RADIO_STREAM_URL
    rename_mock.assert_awaited_once_with(channel, "rap")
    interaction.response.send_message.assert_awaited_once()

    interaction.response.send_message.reset_mock()
    rename_mock.reset_mock()

    await RadioCog.radio_rap.callback(cog, interaction)

    assert cog.stream_url == RADIO_STREAM_URL
    assert cog._previous_stream is None
    rename_mock.assert_awaited_once_with(channel, "Radio")
    interaction.response.send_message.assert_awaited_once()

