from __future__ import annotations

import logging
from typing import Any, Callable
from urllib.parse import urlparse

import yt_dlp
from discord.ext import commands


logger = logging.getLogger(__name__)


_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
_FALLBACK_PLAYER_CLIENTS = ["android_vr", "web_embedded"]


def _is_youtube_target(target: str) -> bool:
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"}:
        # Music2 resolves plain text through ytsearch1, so a non-URL target is
        # necessarily a YouTube search in the current implementation.
        return True
    return (parsed.hostname or "").lower() in _YOUTUBE_HOSTS


def _extract_with_youtube_fallback(target: str) -> dict[str, Any]:
    is_url = urlparse(target).scheme in {"http", "https"}
    lookup = target if is_url else f"ytsearch1:{target}"
    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {
                "player_client": list(_FALLBACK_PLAYER_CLIENTS),
            }
        },
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(lookup, download=False)
        if info is None:
            raise RuntimeError("Aucun résultat")
        entries = info.get("entries") if hasattr(info, "get") else None
        if entries is not None:
            info = next((entry for entry in entries if entry), None)
        if info is None or not hasattr(info, "get"):
            raise RuntimeError("Aucun résultat exploitable")
        return dict(info)


class Music2YoutubeFallbackCog(commands.Cog):
    """Retry failed Music2 YouTube extraction with resilient player clients."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._music: Any | None = None
        self._original_extract: Callable[[str], dict[str, Any]] | None = None
        self._wrapped_extract: Callable[[str], dict[str, Any]] | None = None

    async def cog_load(self) -> None:
        self._install_if_possible()

    def cog_unload(self) -> None:
        self._restore_original()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self._install_if_possible()

    def _install_if_possible(self) -> bool:
        music = self.bot.get_cog("Music2Cog")
        if music is None:
            return False

        current = getattr(music, "_extract_info_sync", None)
        if not callable(current):
            logger.warning("[music2-fallback] extracteur Music2 introuvable")
            return False

        if music is self._music and current is self._wrapped_extract:
            return True

        # If a Music2Cog instance was replaced during an extension reload,
        # restore the previous instance before wrapping the new one.
        self._restore_original()
        original = current

        def wrapped(target: str) -> dict[str, Any]:
            try:
                return original(target)
            except Exception as first_error:
                if not _is_youtube_target(target):
                    raise
                logger.warning(
                    "[music2-fallback] extraction standard échouée (%s); "
                    "nouvelle tentative via %s",
                    first_error,
                    ", ".join(_FALLBACK_PLAYER_CLIENTS),
                )
                try:
                    return _extract_with_youtube_fallback(target)
                except Exception as fallback_error:
                    logger.warning(
                        "[music2-fallback] extraction de secours échouée: %s",
                        fallback_error,
                    )
                    raise fallback_error from first_error

        music._extract_info_sync = wrapped
        self._music = music
        self._original_extract = original
        self._wrapped_extract = wrapped
        logger.info(
            "[music2-fallback] fallback YouTube actif (%s)",
            ", ".join(_FALLBACK_PLAYER_CLIENTS),
        )
        return True

    def _restore_original(self) -> None:
        if self._music is not None and self._original_extract is not None:
            current = getattr(self._music, "_extract_info_sync", None)
            if current is self._wrapped_extract:
                self._music._extract_info_sync = self._original_extract
        self._music = None
        self._original_extract = None
        self._wrapped_extract = None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music2YoutubeFallbackCog(bot))
