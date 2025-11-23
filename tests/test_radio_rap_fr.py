import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
import cogs.radio as radio_mod
from cogs.radio import RadioCog
from config import RADIO_RAP_FR_STREAM_URL, RADIO_STREAM_URL, RADIO_VC_ID


@pytest.mark.asyncio
async def test_radio_rap_fr_toggles_stream(monkeypatch):
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

    class FakeResponse:
        def __init__(self) -> None:
            self._done = False
            self.send_message = AsyncMock()
            self.defer = AsyncMock(side_effect=self._mark_done)

        def _mark_done(self, *args, **kwargs):
            self._done = True

        def is_done(self) -> bool:
            return self._done

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=FakeResponse(),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.radio_rap_fr(interaction)

    assert cog.stream_url == RADIO_RAP_FR_STREAM_URL
    assert cog._previous_stream == RADIO_STREAM_URL
    rename_mock.assert_awaited_once_with(channel, "🟣・Radio-Rap-FR")
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()

    interaction.response = FakeResponse()
    interaction.followup.send.reset_mock()
    rename_mock.reset_mock()

    await cog.radio_rap_fr(interaction)

    assert cog.stream_url == RADIO_STREAM_URL
    assert cog._previous_stream is None
    rename_mock.assert_awaited_once_with(channel, "📻・Radio-HipHop")
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
