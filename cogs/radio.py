import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    RADIO_MUTED_ROLE_ID,
    RADIO_RAP_STREAM_URL,
    RADIO_STREAM_URL,
    RADIO_VC_ID,
)
from utils.rename_manager import rename_manager

FFMPEG_BEFORE = "-fflags nobuffer -probesize 32k"
FFMPEG_OPTIONS = "-filter:a loudnorm"


class RadioCog(commands.Cog):
    """Lit un flux radio dans un salon vocal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.vc_id = RADIO_VC_ID
        self.stream_url: Optional[str] = RADIO_STREAM_URL
        self.voice: Optional[discord.VoiceClient] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._original_name: Optional[str] = None

    async def _connect_and_play(self) -> None:
        if not self.stream_url:
            logging.warning("RADIO_STREAM_URL non défini")
            return
        channel = self.bot.get_channel(self.vc_id)
        if not isinstance(channel, discord.VoiceChannel):
            logging.warning("Salon radio %s introuvable", self.vc_id)
            return
        if self.voice is None or not self.voice.is_connected():
            try:
                self.voice = await channel.connect(reconnect=True)
            except discord.Forbidden:
                logging.warning(
                    "Permissions insuffisantes pour se connecter au salon radio %s",
                    self.vc_id,
                )
                return
            except discord.NotFound:
                logging.warning(
                    "Salon radio %s introuvable lors de la connexion",
                    self.vc_id,
                )
                return
            except discord.HTTPException as e:
                logging.error(
                    "Erreur HTTP lors de la connexion au salon radio: %s",
                    e,
                )
                return
            except Exception as e:
                logging.exception(
                    "Connexion au salon radio échouée: %s",
                    e,
                )
                return
        if self.voice and not self.voice.is_playing():
            source = discord.FFmpegPCMAudio(
                self.stream_url,
                before_options=FFMPEG_BEFORE,
                options=FFMPEG_OPTIONS,
            )
            self.voice.play(source, after=self._after_play)

    def _after_play(self, error: Optional[Exception]) -> None:
        if error:
            logging.warning("Erreur de lecture radio: %s", error)
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.bot.loop.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self) -> None:
        await asyncio.sleep(5)
        await self._connect_and_play()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        channel = self.bot.get_channel(self.vc_id)
        if isinstance(channel, discord.VoiceChannel):
            self._original_name = channel.name
        await self._connect_and_play()

    @app_commands.command(name="radio_rap", description="Basculer la radio sur le flux rap")
    @app_commands.checks.cooldown(1, 3600, key=lambda i: i.user.id)
    async def radio_rap(self, interaction: discord.Interaction) -> None:
        if self.stream_url == RADIO_RAP_STREAM_URL:
            self.stream_url = RADIO_STREAM_URL
            if self.voice and self.voice.is_playing():
                self.voice.stop()
            await self._connect_and_play()
            channel = self.bot.get_channel(self.vc_id)
            if isinstance(channel, discord.VoiceChannel) and self._original_name:
                await rename_manager.request(channel, self._original_name)
            await interaction.response.send_message(
                "Radio remise sur la station d'origine"
            )
        else:
            self.stream_url = RADIO_RAP_STREAM_URL
            if self.voice and self.voice.is_playing():
                self.voice.stop()
            await self._connect_and_play()
            channel = self.bot.get_channel(self.vc_id)
            if isinstance(channel, discord.VoiceChannel):
                await rename_manager.request(channel, "rap")
            await interaction.response.send_message("Radio changée pour rap")

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

        if any(role.id == RADIO_MUTED_ROLE_ID for role in member.roles):
            # Join radio channel -> mute
            if after.channel and after.channel.id == self.vc_id:
                try:
                    await member.edit(mute=True)
                except discord.Forbidden:
                    logging.warning(
                        "Permissions insuffisantes pour mute %s", member
                    )
                except discord.NotFound:
                    logging.warning("Membre introuvable pour mute %s", member)
                except discord.HTTPException as e:
                    logging.error(
                        "Erreur HTTP lors du mute de %s: %s", member, e
                    )
                except Exception as e:
                    logging.exception(
                        "Impossible de mute %s: %s", member, e
                    )
            # Leave radio channel -> unmute
            elif before.channel and before.channel.id == self.vc_id:
                try:
                    await member.edit(mute=False)
                except discord.Forbidden:
                    logging.warning(
                        "Permissions insuffisantes pour demute %s", member
                    )
                except discord.NotFound:
                    logging.warning(
                        "Membre introuvable pour demute %s", member
                    )
                except discord.HTTPException as e:
                    logging.error(
                        "Erreur HTTP lors du demute de %s: %s", member, e
                    )
                except Exception as e:
                    logging.exception(
                        "Impossible de demute %s: %s", member, e
                    )

    def cog_unload(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self.voice and self.voice.is_connected():
            self.bot.loop.create_task(self.voice.disconnect())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RadioCog(bot))
