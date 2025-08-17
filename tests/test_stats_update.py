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
    def __init__(self, status, bot=False):
        self.status = status
        self.bot = bot


class DummyVoiceChannel:
    def __init__(self, members):
        self.members = members


class DummyGuild:
    def __init__(self, members, category, voice_channels):
        self.members = members
        self._category = category
        self.voice_channels = voice_channels

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
    ch3 = DummyChannel()
    category = DummyCategory([ch1, ch2, ch3])
    voice_channel = DummyVoiceChannel(
        [DummyMember(discord.Status.online), DummyMember(discord.Status.online, bot=True)]
    )
    guild = DummyGuild(
        [
            DummyMember(discord.Status.online),
            DummyMember(discord.Status.offline),
            DummyMember(discord.Status.online, bot=True),
        ],
        category,
        [voice_channel],
    )

    async def fake_safe_channel_edit(channel, **kwargs):
        channel.name = kwargs.get("name")

    monkeypatch.setattr("cogs.stats.safe_channel_edit", fake_safe_channel_edit)

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = StatsCog(bot)
    cog.refresh_stats.cancel()

    await cog.update_stats(guild)

    members = sum(1 for m in guild.members if not m.bot)
    assert ch1.name == f"👥 Membres : {members}"
    online = sum(1 for m in guild.members if not m.bot and m.status != discord.Status.offline)
    assert ch2.name == f"🟢 En ligne : {online}"
    voice = sum(len([m for m in vc.members if not m.bot]) for vc in guild.voice_channels)
    assert ch3.name == f"🔊 Voc : {voice}"

    await bot.close()
