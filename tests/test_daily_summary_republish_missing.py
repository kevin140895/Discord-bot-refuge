import discord
import pytest
from discord.ext import commands
from unittest import mock

from cogs.daily_summary_poster import DailySummaryPoster


class DummyChannel:
    def __init__(self):
        self.sent_contents = []

    async def send(self, content):
        self.sent_contents.append(content)
        return mock.Mock(id=456)

    async def fetch_message(self, mid):
        raise discord.NotFound(mock.Mock(status=404), "Not Found")


@pytest.mark.asyncio
async def test_republish_when_message_missing():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    channel = DummyChannel()
    bot.get_channel = lambda _cid: channel

    summary = {"date": "today", "message_id": 123}

    cog = DailySummaryPoster.__new__(DailySummaryPoster)
    cog.bot = bot
    cog._read_summary = lambda: summary
    cog._write_summary = lambda data: summary.update(data)
    cog._build_message = lambda data: "message"

    await cog._maybe_post({"date": "today"})

    assert summary == {"date": "today", "message_id": 456}
    assert channel.sent_contents == ["message"]

    await bot.close()
