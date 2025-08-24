import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    RADIO_RAP_FR_STREAM_URL,
    RADIO_RAP_STREAM_URL,
    RADIO_STREAM_URL,
    RADIO_TEXT_CHANNEL_ID,
    RADIO_VC_ID,
    ROCK_RADIO_STREAM_URL,
)
from utils.rename_manager import rename_manager
from utils.voice import ensure_voice, play_stream
from view import RadioView
logger = logging.getLogger(__name__)


class RadioCog(commands.Cog):
    """Lit un flux radio dans un salon vocal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.vc_id = RADIO_VC_ID
        self.stream_url: Optional[str] = RADIO_STREAM_URL
        self.voice: Optional[discord.VoiceClient] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._original_name: Optional[str] = None
        self._previous_stream: Optional[str] = None

    async def _connect_and_play(self) -> None:
        if not self.stream_url:
            logger.warning("RADIO_STREAM_URL non défini")
            return
        self.voice = await ensure_voice(self.bot, self.vc_id, self.voice)
        play_stream(self.voice, self.stream_url, after=self._after_play)

    def _after_play(self, error: Optional[Exception]) -> None:
        if error:
            logger.warning("Erreur de lecture radio: %s", error)
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.bot.loop.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self) -> None:
        await asyncio.sleep(5)
        await self._connect_and_play()

    async def _ensure_radio_message(
        self, channel: discord.abc.Messageable
    ) -> None:
        try:
            async for msg in channel.history(limit=50):
                if msg.author.id != self.bot.user.id:
                    continue
                for row in msg.components:
                    for comp in row.children:
                        if (
                            isinstance(comp, discord.ui.Button)
                            and comp.custom_id == "radio_24"
                        ):
                            return
        except Exception as e:
            logger.warning("Impossible de vérifier le message radio: %s", e)
            return
        await channel.send("Changement de radio", view=RadioView())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        voice_channel = self.bot.get_channel(self.vc_id)
        if isinstance(voice_channel, discord.VoiceChannel):
            self._original_name = voice_channel.name

        text_channel = self.bot.get_channel(RADIO_TEXT_CHANNEL_ID)
        if isinstance(text_channel, discord.abc.Messageable):
            await self._ensure_radio_message(text_channel)
        await self._connect_and_play()

    async def _rename_for_stream(
        self, channel: discord.VoiceChannel, stream_url: str
    ) -> None:
        if stream_url == RADIO_RAP_STREAM_URL:
            await rename_manager.request(channel, "🔘・Radio-Rap")
        elif stream_url == ROCK_RADIO_STREAM_URL:
            await rename_manager.request(channel, "☢️・Radio-Rock")
        elif stream_url == RADIO_RAP_FR_STREAM_URL:
            await rename_manager.request(channel, "🔴・Radio-RapFR")
        elif stream_url == RADIO_STREAM_URL:
            await rename_manager.request(channel, "📻・Radio-HipHop")
        else:
            if self._original_name:
                await rename_manager.request(channel, self._original_name)

    @app_commands.command(name="radio_rap", description="Basculer la radio sur le flux rap")
    async def radio_rap(self, interaction: discord.Interaction) -> None:
        channel = self.bot.get_channel(self.vc_id)

        if self.stream_url == RADIO_RAP_STREAM_URL and self._previous_stream:
            self.stream_url = self._previous_stream
            self._previous_stream = None
            if self.voice and self.voice.is_playing():
                self.voice.stop()
            await self._connect_and_play()
            if isinstance(channel, discord.VoiceChannel):
                await self._rename_for_stream(channel, self.stream_url)
            await interaction.response.send_message(
                "Radio changée pour la station précédente"
            )
            return

        self._previous_stream = self.stream_url
        self.stream_url = RADIO_RAP_STREAM_URL
        if self.voice and self.voice.is_playing():
            self.voice.stop()
        await self._connect_and_play()
        if isinstance(channel, discord.VoiceChannel):
            await self._rename_for_stream(channel, RADIO_RAP_STREAM_URL)
        await interaction.response.send_message("Radio changée pour rap")

    @app_commands.command(name="radio_rock", description="Basculer la radio sur le flux rock")
    async def radio_rock(self, interaction: discord.Interaction) -> None:
        channel = self.bot.get_channel(self.vc_id)

        if self.stream_url == ROCK_RADIO_STREAM_URL and self._previous_stream:
            self.stream_url = self._previous_stream
            self._previous_stream = None
            if self.voice and self.voice.is_playing():
                self.voice.stop()
            await self._connect_and_play()
            if isinstance(channel, discord.VoiceChannel):
                await self._rename_for_stream(channel, self.stream_url)
            await interaction.response.send_message(
                "Radio changée pour la station précédente"
            )
            return

        self._previous_stream = self.stream_url
        self.stream_url = ROCK_RADIO_STREAM_URL
        if self.voice and self.voice.is_playing():
            self.voice.stop()
        await self._connect_and_play()
        if isinstance(channel, discord.VoiceChannel):
            await self._rename_for_stream(channel, ROCK_RADIO_STREAM_URL)
        await interaction.response.send_message("Radio changée pour rock")

    @app_commands.command(
        name="radio_rapfr", description="Basculer la radio sur le flux rap français"
    )
    async def radio_rapfr(self, interaction: discord.Interaction) -> None:
        channel = self.bot.get_channel(self.vc_id)

        if self.stream_url == RADIO_RAP_FR_STREAM_URL and self._previous_stream:
            self.stream_url = self._previous_stream
            self._previous_stream = None
            if self.voice and self.voice.is_playing():
                self.voice.stop()
            await self._connect_and_play()
            if isinstance(channel, discord.VoiceChannel):
                await self._rename_for_stream(channel, self.stream_url)
            await interaction.response.send_message(
                "Radio changée pour la station précédente"
            )
            return

        self._previous_stream = self.stream_url
        self.stream_url = RADIO_RAP_FR_STREAM_URL
        if self.voice and self.voice.is_playing():
            self.voice.stop()
        await self._connect_and_play()
        if isinstance(channel, discord.VoiceChannel):
            await self._rename_for_stream(channel, RADIO_RAP_FR_STREAM_URL)
        await interaction.response.send_message("Radio changée pour rap français")

    @app_commands.command(
        name="radio_24", description="Revenir sur l'ancienne radio 24/7"
    )
    async def radio_24(self, interaction: discord.Interaction) -> None:
        channel = self.bot.get_channel(self.vc_id)
        self.stream_url = RADIO_STREAM_URL
        self._previous_stream = None
        if self.voice and self.voice.is_playing():
            self.voice.stop()
        await self._connect_and_play()
        if isinstance(channel, discord.VoiceChannel):
            await self._rename_for_stream(channel, RADIO_STREAM_URL)
        await interaction.response.send_message(
            "Radio changée pour la station 24/7"
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id == self.bot.user.id and after.channel is None:
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = self.bot.loop.create_task(
                    self._delayed_reconnect()
                )
            return

        # Previously, members with a specific role were automatically muted when
        # joining the radio channel and unmuted when leaving it. This behaviour
        # has been removed to ensure that the bot no longer alters voice states
        # based on a role.

    def cog_unload(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self.voice and self.voice.is_connected():
            self.bot.loop.create_task(self.voice.disconnect())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RadioCog(bot))
