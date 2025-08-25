import discord
import pytest
from discord.ext import commands

import config
from cogs.stats import StatsCog


class DummyChannel:
    def __init__(self):
        self.name: str | None = None


class DummyMember:
    def __init__(self, status, bot=False):
        self.status = status
        self.bot = bot


class DummyVoiceChannel:
    def __init__(self, members):
        self.members = members


class DummyGuild:
    def __init__(self, members, channels, voice_channels, member_count=None):
        self.members = members
        self._channels = channels
        self.voice_channels = voice_channels
        self.member_count = (
            member_count if member_count is not None else len(self.members)
        )

    def get_channel(self, cid):
        return self._channels.get(cid)


@pytest.mark.asyncio
async def test_update_stats_changes_channel_names(monkeypatch):
    ch1 = DummyChannel()
    ch2 = DummyChannel()
    ch3 = DummyChannel()
    channels = {
        config.STATS_MEMBERS_CHANNEL_ID: ch1,
        config.STATS_ONLINE_CHANNEL_ID: ch2,
        config.STATS_VOICE_CHANNEL_ID: ch3,
    }
    voice_channel = DummyVoiceChannel(
        [DummyMember(discord.Status.online), DummyMember(discord.Status.online, bot=True)]
    )
    guild = DummyGuild(
        [
            DummyMember(discord.Status.online),
            DummyMember(discord.Status.offline),
            DummyMember(discord.Status.online, bot=True),
        ],
        channels,
        [voice_channel],
        member_count=5,
    )

    async def fake_request(channel, name):
        channel.name = name

    monkeypatch.setattr("cogs.stats.rename_manager.request", fake_request)

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = StatsCog(bot)
    cog.refresh_members.cancel()
    cog.refresh_online.cancel()
    cog.refresh_voice.cancel()

    await cog.update_members(guild)
    await cog.update_online(guild)
    await cog.update_voice(guild)

    members = guild.member_count - sum(1 for m in guild.members if m.bot)
    assert ch1.name == f"👥 Membres : {members}"
    online = sum(
        1 for m in guild.members if not m.bot and m.status != discord.Status.offline
    )
    assert ch2.name == f"🟢 En ligne : {online}"
    voice = sum(len([m for m in vc.members if not m.bot]) for vc in guild.voice_channels)
    assert ch3.name == f"🔊 Voc : {voice}"

    await bot.close()
