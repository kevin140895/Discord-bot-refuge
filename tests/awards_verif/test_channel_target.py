from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.daily_awards as daily_awards
from cogs.daily_awards import DailyAwards


@pytest.mark.asyncio
async def test_award_channel_id(monkeypatch):
    channel_id = 1400552164979507263

    # Patch constants
    monkeypatch.setattr(daily_awards, "AWARD_ANNOUNCE_CHANNEL_ID", channel_id)
    monkeypatch.setattr(daily_awards, "GUILD_ID", 1)

    channel = SimpleNamespace(id=channel_id, send=AsyncMock())
    guild = SimpleNamespace(fetch_channel=AsyncMock(return_value=channel), me=SimpleNamespace(), text_channels=[])
    bot = SimpleNamespace(get_channel=lambda _: None, get_guild=lambda _: guild)

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = bot

    result = await DailyAwards._get_announce_channel(cog)
    guild.fetch_channel.assert_awaited_once_with(channel_id)
    assert result is channel
