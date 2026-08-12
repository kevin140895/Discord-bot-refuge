from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands, tasks

from config import (
    RADIO_RAP_FR_STREAM_URL,
    RADIO_RAP_STREAM_URL,
    RADIO_STREAM_URL,
    RADIO_VC_ID,
    ROCK_RADIO_STREAM_URL,
)
from utils.rename_manager import rename_manager


logger = logging.getLogger(__name__)

_RECORDING_PREFIX = "🔴・"
_VOICE_STATUS_MAX_LENGTH = 500
_TOPIC_SUFFIX_RE = re.compile(r"\s*-\s*topic\s*$", re.IGNORECASE)
_VEVO_SUFFIX_RE = re.compile(r"\s*vevo\s*$", re.IGNORECASE)
_TITLE_SEPARATORS = (" - ", " – ", " — ")
_STREAM_STATUSES = {
    RADIO_RAP_STREAM_URL: "🔘 Radio Rap US",
    RADIO_RAP_FR_STREAM_URL: "🟣 Radio Rap FR",
    ROCK_RADIO_STREAM_URL: "☢️ Radio Rock",
    RADIO_STREAM_URL: "📻 Radio Hip-Hop",
}


class Music2DynamicRenameCog(commands.Cog):
    """Keep the shared radio voice channel display aligned with active audio."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_requested_name: str | None = None
        self._last_requested_status: str | None = None
        self._last_radio_stream: str | None = None

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

    @classmethod
    def _voice_status_for_track(cls, track: object) -> str:
        title = " ".join(str(getattr(track, "title", "") or "").split()).strip()
        if not title:
            title = cls._artist_for_track(track)
        return f"🎵 {title}"[:_VOICE_STATUS_MAX_LENGTH]

    @staticmethod
    def _voice_status_for_stream(stream_url: str) -> str:
        return _STREAM_STATUSES.get(stream_url, "📻 Radio")

    async def _request_voice_status(
        self, channel: discord.VoiceChannel, status: str
    ) -> None:
        normalized = " ".join(str(status).split()).strip()[:_VOICE_STATUS_MAX_LENGTH]
        if not normalized or normalized == self._last_requested_status:
            return

        try:
            await channel.edit(
                status=normalized,
                reason="RefugeBot: affichage dynamique Radio",
            )
        except discord.Forbidden:
            logger.warning(
                "[music2_status] permission SET_VOICE_CHANNEL_STATUS manquante pour %s",
                channel.id,
            )
            return
        except discord.HTTPException as exc:
            logger.warning(
                "[music2_status] mise à jour du statut vocal impossible pour %s: %s",
                channel.id,
                exc,
            )
            return

        self._last_requested_status = normalized

    async def _sync_current_track_name(self) -> None:
        music = self.bot.get_cog("Music2Cog")
        radio = self.bot.get_cog("RadioCog")
        if music is None or radio is None:
            self._last_requested_name = None
            self._last_requested_status = None
            self._last_radio_stream = None
            return

        channel = self.bot.get_channel(RADIO_VC_ID)
        if not isinstance(channel, discord.VoiceChannel):
            return

        stream_url = getattr(radio, "stream_url", None)
        if stream_url is not None:
            self._last_requested_name = None
            await self._request_voice_status(
                channel,
                self._voice_status_for_stream(stream_url),
            )
            if stream_url == self._last_radio_stream:
                return

            rename_for_stream = getattr(radio, "_rename_for_stream", None)
            if callable(rename_for_stream):
                await rename_for_stream(channel, stream_url)
                self._last_radio_stream = stream_url
            return

        self._last_radio_stream = None
        track = getattr(music, "current", None)
        if track is None:
            self._last_requested_name = None
            return

        voice = getattr(radio, "voice", None)
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            return

        await self._request_voice_status(
            channel,
            self._voice_status_for_track(track),
        )

        new_name = self._channel_name_for_track(track)
        if new_name == self._last_requested_name:
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
