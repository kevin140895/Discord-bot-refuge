from __future__ import annotations

import re

import discord
from discord.ext import commands, tasks

from config import RADIO_VC_ID
from utils.rename_manager import rename_manager


_RECORDING_PREFIX = "🔴・"
_TOPIC_SUFFIX_RE = re.compile(r"\s*-\s*topic\s*$", re.IGNORECASE)
_VEVO_SUFFIX_RE = re.compile(r"\s*vevo\s*$", re.IGNORECASE)
_TITLE_SEPARATORS = (" - ", " – ", " — ")


class Music2DynamicRenameCog(commands.Cog):
    """Rename the shared radio voice channel while custom music is playing."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_requested_name: str | None = None

    async def cog_load(self) -> None:
        self.sync_name.start()

    def cog_unload(self) -> None:
        self.sync_name.cancel()

    @staticmethod
    def _clean_artist(value: str | None) -> str:
        if not value:
            return ""
        artist = " ".join(str(value).split()).strip()
        artist = _TOPIC_SUFFIX_RE.sub("", artist).strip()
        artist = _VEVO_SUFFIX_RE.sub("", artist).strip()
        return artist

    @classmethod
    def _artist_for_track(cls, track: object) -> str:
        uploader = cls._clean_artist(getattr(track, "uploader", None))
        if uploader:
            return uploader

        title = " ".join(str(getattr(track, "title", "") or "").split()).strip()
        for separator in _TITLE_SEPARATORS:
            if separator in title:
                candidate = cls._clean_artist(title.split(separator, 1)[0])
                if candidate:
                    return candidate
        return title or "Musique"

    @classmethod
    def _channel_name_for_track(cls, track: object) -> str:
        artist = cls._artist_for_track(track)
        max_artist_length = 100 - len(_RECORDING_PREFIX)
        return f"{_RECORDING_PREFIX}{artist[:max_artist_length]}"

    async def _sync_current_track_name(self) -> None:
        music = self.bot.get_cog("Music2Cog")
        radio = self.bot.get_cog("RadioCog")
        if music is None or radio is None:
            self._last_requested_name = None
            return

        track = getattr(music, "current", None)
        if track is None or getattr(radio, "stream_url", None) is not None:
            self._last_requested_name = None
            return

        voice = getattr(radio, "voice", None)
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            return

        channel = self.bot.get_channel(RADIO_VC_ID)
        if not isinstance(channel, discord.VoiceChannel):
            return

        new_name = self._channel_name_for_track(track)
        if new_name == self._last_requested_name and channel.name == new_name:
            return

        self._last_requested_name = new_name
        await rename_manager.request(channel, new_name)

    @tasks.loop(seconds=1.0)
    async def sync_name(self) -> None:
        await self._sync_current_track_name()

    @sync_name.before_loop
    async def before_sync_name(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music2DynamicRenameCog(bot))
