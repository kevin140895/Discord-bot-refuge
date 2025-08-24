import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from config import ROCK_RADIO_STREAM_URL, ROCK_RADIO_VC_ID
from utils.voice import ensure_voice, play_stream
logger = logging.getLogger(__name__)


class RockRadioCog(commands.Cog):
    """Lit un flux radio rock dans un salon vocal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.vc_id = ROCK_RADIO_VC_ID
        self.stream_url: Optional[str] = ROCK_RADIO_STREAM_URL
        self.voice: Optional[discord.VoiceClient] = None
        self._reconnect_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self._connect_and_play()

    async def _connect_and_play(self) -> None:
        if not self.stream_url:
            logger.warning("ROCK_RADIO_STREAM_URL non défini")
            return
        self.voice = await ensure_voice(self.bot, self.vc_id, self.voice)
        play_stream(self.voice, self.stream_url, after=self._after_play)

    def _after_play(self, error: Optional[Exception]) -> None:
        if error:
            logger.warning("Erreur de lecture rock radio: %s", error)
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self) -> None:
        await asyncio.sleep(5)
        await self._connect_and_play()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._connect_and_play()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id == self.bot.user.id and after.channel is None:
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(
                    self._delayed_reconnect()
                )
            return

        # Previously, members with a specific role were automatically muted when
        # joining the rock radio channel and unmuted when leaving it. This logic
        # has been removed so the bot no longer alters member voice states based
        # on their roles.

    def cog_unload(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self.voice and self.voice.is_connected():
            asyncio.create_task(self.voice.disconnect())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RockRadioCog(bot))
