import discord
import pytest
from discord.ext import commands
from unittest import mock

from cogs.daily_summary_poster import DailySummaryPoster
from config import ACTIVITY_SUMMARY_CH


@pytest.mark.asyncio
async def test_fetch_channel_failure_writes_persistence():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.get_channel = lambda _cid: None
    bot.fetch_channel = mock.AsyncMock(
        side_effect=discord.NotFound(mock.Mock(status=404), "Not Found")
    )

    written = {}

    cog = DailySummaryPoster.__new__(DailySummaryPoster)
    cog.bot = bot
    cog._read_summary = lambda: {}

    def fake_write(data):
        written.update(data)
    cog._write_summary = fake_write
    cog._build_message = lambda data: "message"

    await cog._maybe_post({"date": "today"})

    bot.fetch_channel.assert_awaited_once_with(ACTIVITY_SUMMARY_CH)
    assert written == {"date": "today", "error": "channel_not_found"}

    await bot.close()
