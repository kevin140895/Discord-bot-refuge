import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.rock_radio import RockRadioCog
from config import ROCK_RADIO_VC_ID


@pytest.mark.asyncio
async def test_move_to_rock_channel_when_connected_elsewhere(monkeypatch):
    loop = asyncio.get_event_loop()

    class DummyVoiceChannel(SimpleNamespace):
        pass

    monkeypatch.setattr("cogs.rock_radio.discord.VoiceChannel", DummyVoiceChannel)
    monkeypatch.setattr("utils.voice.discord.VoiceChannel", DummyVoiceChannel)
    channel = DummyVoiceChannel(id=ROCK_RADIO_VC_ID)
    bot = SimpleNamespace(
        loop=loop,
        get_channel=lambda _id: channel,
        fetch_channel=AsyncMock(return_value=channel),
    )
    cog = RockRadioCog(bot)
    cog.voice = SimpleNamespace(
        is_connected=lambda: True,
        channel=SimpleNamespace(id=123),
        move_to=AsyncMock(),
        is_playing=lambda: True,
    )
    await cog._connect_and_play()
    cog.voice.move_to.assert_awaited_once_with(channel)
