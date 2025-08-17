import discord
import pytest
from discord.ext import commands

import config
from cogs.stats import StatsCog


class DummyChannel:
    def __init__(self):
        self.name: str | None = None


class DummyCategory:
    def __init__(self, channels):
        self.channels = channels


class DummyMember:
    def __init__(self, status):
        self.status = status


class DummyGuild:
    def __init__(self, members, category):
        self.members = members
        self._category = category

    @property
    def member_count(self):
        return len(self.members)

    def get_channel(self, cid):
        if cid == config.STATS_CATEGORY_ID:
            return self._category
        return None


@pytest.mark.asyncio
async def test_update_stats_changes_channel_names(monkeypatch):
    ch1 = DummyChannel()
    ch2 = DummyChannel()
    category = DummyCategory([ch1, ch2])
    guild = DummyGuild(
        [DummyMember(discord.Status.online), DummyMember(discord.Status.offline)],
        category,
    )

    async def fake_safe_channel_edit(channel, **kwargs):
        channel.name = kwargs.get("name")

    monkeypatch.setattr("cogs.stats.safe_channel_edit", fake_safe_channel_edit)

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = StatsCog(bot)
    cog.refresh_stats.cancel()

    await cog.update_stats(guild)

    assert ch1.name == f"Members: {guild.member_count}"
    online = sum(1 for m in guild.members if m.status != discord.Status.offline)
    assert ch2.name == f"Online: {online}"

    await bot.close()
