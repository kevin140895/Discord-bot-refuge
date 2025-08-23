import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.radio import RadioCog
from config import RADIO_TEXT_CHANNEL_ID, RADIO_VC_ID


@pytest.mark.asyncio
async def test_on_ready_posts_control_message(monkeypatch):
    class DummyVoiceChannel:
        def __init__(self):
            self.id = RADIO_VC_ID
            self.name = "Radio"

    class DummyTextChannel:
        def __init__(self):
            self.id = RADIO_TEXT_CHANNEL_ID
            self.send = AsyncMock()

        async def history(self, limit=50):
            for _ in []:
                yield _

    voice_channel = DummyVoiceChannel()
    text_channel = DummyTextChannel()

    def get_channel(cid):
        if cid == RADIO_VC_ID:
            return voice_channel
        if cid == RADIO_TEXT_CHANNEL_ID:
            return text_channel
        return None

    bot = SimpleNamespace(
        user=SimpleNamespace(id=1),
        loop=asyncio.get_event_loop(),
        get_channel=get_channel,
    )

    monkeypatch.setattr("cogs.radio.discord.VoiceChannel", DummyVoiceChannel)
    monkeypatch.setattr("cogs.radio.discord.abc.Messageable", object)
    connect_mock = AsyncMock()
    monkeypatch.setattr(RadioCog, "_connect_and_play", connect_mock)

    cog = RadioCog(bot)
    await cog.on_ready()

    text_channel.send.assert_awaited_once()
    connect_mock.assert_awaited_once()
