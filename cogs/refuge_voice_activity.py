from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from services.refuge_voice_activity import CommunityVoiceTracker
from storage.refuge_activity_store import refuge_activity_store


logger = logging.getLogger(__name__)
CHECKPOINT_SECONDS = 30


class RefugeVoiceActivityCog(commands.Cog):
    """Track shared human voice-channel time for the Refuge world."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tracker = CommunityVoiceTracker(refuge_activity_store)

    async def cog_load(self) -> None:
        await refuge_activity_store.initialize()
        self.checkpoint_community_voice.start()

    def cog_unload(self) -> None:
        self.checkpoint_community_voice.cancel()
        try:
            asyncio.create_task(self.tracker.checkpoint())
        except RuntimeError:
            pass

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        channels: list[tuple[int, tuple[discord.Member, ...]]] = []
        excluded_channel_ids: set[int] = set()

        for guild in self.bot.guilds:
            if guild.afk_channel is not None:
                excluded_channel_ids.add(guild.afk_channel.id)
            channels.extend(
                (channel.id, tuple(channel.members))
                for channel in guild.voice_channels
            )

        await self.tracker.reconcile_snapshot(
            channels,
            excluded_channel_ids=excluded_channel_ids,
            at=datetime.now(timezone.utc),
        )

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        try:
            await self.tracker.stop_all(at=datetime.now(timezone.utc))
        except Exception:
            logger.exception(
                "[refuge] Impossible de finaliser le temps vocal communautaire "
                "pendant la déconnexion"
            )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or before.channel == after.channel:
            return

        afk_channel_id = (
            member.guild.afk_channel.id
            if member.guild.afk_channel is not None
            else None
        )
        affected: dict[int, discord.VoiceChannel] = {}
        for channel in (before.channel, after.channel):
            if not isinstance(channel, discord.VoiceChannel):
                continue
            if afk_channel_id is not None and channel.id == afk_channel_id:
                continue
            affected[channel.id] = channel

        now = datetime.now(timezone.utc)
        for channel in affected.values():
            await self.tracker.reconcile_channel(
                channel.id,
                tuple(channel.members),
                at=now,
            )

    @tasks.loop(seconds=CHECKPOINT_SECONDS)
    async def checkpoint_community_voice(self) -> None:
        try:
            await self.tracker.checkpoint(at=datetime.now(timezone.utc))
        except Exception:
            logger.exception(
                "[refuge] Impossible de sauvegarder le temps vocal communautaire"
            )

    @checkpoint_community_voice.before_loop
    async def before_checkpoint_community_voice(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefugeVoiceActivityCog(bot))
