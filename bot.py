"""Main bot implementation for tests.

This module provides a minimal :class:`RefugeBot` used in the test
suite.  It exposes a few global helpers (``xp_store``
``rename_manager`` etc.) so tests can monkeypatch them and verify
interaction with the bot.
"""

from __future__ import annotations

import logging
import pkgutil
from typing import Final

import discord
from discord.ext import commands

from config import DISABLED_COGS, GUILD_ID, LEVEL_FEED_CHANNEL_ID
import cogs

from storage.xp_store import xp_store
from ui.radio_view import RadioView
from utils.api_meter import api_meter
from utils.background_tasks import background_tasks
from utils.discord_api_trace import create_discord_http_trace
from utils.rename_manager import rename_manager
from utils.rate_limit import GlobalRateLimiter, limiter as _limiter
from utils.ytdlp_auth import configure_ytdlp_auth
from view import PlayerTypeView, StreamerTempVoiceView
from utils import level_feed


# global rate limiter instance
limiter: Final[GlobalRateLimiter] = _limiter
logger = logging.getLogger(__name__)

# The dedicated RockRadioCog is a legacy duplicate. Rock playback is handled by
# RadioCog, which switches the main radio channel to ROCK_RADIO_STREAM_URL.
# Keep the legacy module in the repository for reference/tests, but never load it
# automatically in production.
LEGACY_DISABLED_COGS: Final[frozenset[str]] = frozenset({"rock_radio"})

# Some modules live under ``cogs`` because they support a cog, but are not
# discord.py extensions themselves and therefore do not expose ``setup()``.
# They must never be passed to ``load_extension`` during package autodiscovery.
COG_SUPPORT_MODULES: Final[frozenset[str]] = frozenset({"maitre_du_jeu_ai"})


async def reset_http_error_counter() -> None:
    """Reset the HTTP error counter (placeholder)."""
    # Real implementation would reset metrics.  In tests this coroutine is
    # monkeypatched, so the body can stay empty.
    return None


class RefugeBot(commands.Bot):
    """Discord bot with minimal startup and shutdown logic for tests."""

    def __init__(self, *args, **kwargs) -> None:
        # discord.py creates its shared aiohttp ClientSession from Client
        # options, so the trace must be installed before login/setup_hook.
        kwargs.setdefault("http_trace", create_discord_http_trace(limiter))
        super().__init__(*args, **kwargs)

    async def setup_hook(self) -> None:  # type: ignore[override]
        """Start background helpers and synchronise the command tree."""
        # Configure yt-dlp before any music cog is imported. This makes the same
        # Railway cookie jar available to search, candidate validation and
        # stream resolution without exposing the cookie contents to the cogs.
        configure_ytdlp_auth()

        # Start background helpers. In the real project these are asynchronous
        # coroutines, hence we ``await`` them so the test suite can verify
        # they have been invoked.
        await xp_store.start()
        await rename_manager.start()
        await api_meter.start(self)
        limiter.start()
        await reset_http_error_counter()
        level_feed.setup(self)

        # Load active cogs from the ``cogs`` package so every enabled slash
        # command is registered when the bot starts. Retired/disabled modules
        # stay in the repository for reference but must not start background
        # tasks or register commands. Support-only modules are also excluded
        # because they are not discord.py extensions and expose no ``setup``.
        discovered = list(pkgutil.iter_modules(cogs.__path__))
        loaded_names = set()
        for module in discovered:
            if module.name in COG_SUPPORT_MODULES:
                logger.debug("Skipping cog support module: %s", module.name)
                continue
            if module.name in DISABLED_COGS or module.name in LEGACY_DISABLED_COGS:
                logger.info("Skipping disabled cog: %s", module.name)
                continue
            await self.load_extension(f"{cogs.__name__}.{module.name}")
            loaded_names.add(module.name)

        # Ensure startup-critical cogs are loaded even if package discovery did
        # not return them. DISABLED_COGS remains authoritative: a cog explicitly
        # disabled by configuration must never be reloaded by this fallback.
        for required in (
            "economy_ui",
            "machine_a_sous",
            "temp_vc",
            "streamer_temp_vc",
        ):
            if required in DISABLED_COGS or required in LEGACY_DISABLED_COGS:
                logger.info("Skipping disabled fallback cog: %s", required)
                continue
            if required not in loaded_names:
                await self.load_extension(f"cogs.{required}")

        # Sync application commands. Use guild-specific sync when ``GUILD_ID``
        # is defined so commands appear instantly on that server.
        if GUILD_ID:
            await self.tree.sync(guild=discord.Object(id=GUILD_ID))
        else:
            await self.tree.sync()

        # Register persistent views. ``add_view`` can only be called once per
        # view instance; protect against duplicates when ``setup_hook`` runs
        # multiple times during tests or restarts.
        if not getattr(self, "_player_type_view_added", False):
            self.add_view(PlayerTypeView())
            self._player_type_view_added = True

        if not getattr(self, "_radio_view_added", False):
            self.add_view(RadioView())
            self._radio_view_added = True

        if not getattr(self, "_streamer_temp_vc_view_added", False):
            self.add_view(StreamerTempVoiceView(self))
            self._streamer_temp_vc_view_added = True

    async def announce_level_up(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        old_level: int,
        new_level: int,
        old_xp: int,
        new_xp: int,
    ) -> None:
        """Send a level-up notification to the configured channel.

        The method is invoked by XP-related cogs when a member gains a level.
        It tries to resolve ``LEVEL_FEED_CHANNEL_ID`` within the provided
        guild, and silently returns when the channel cannot be used.
        """

        channel = guild.get_channel(LEVEL_FEED_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.fetch_channel(LEVEL_FEED_CHANNEL_ID)
            except Exception:
                channel = None

        if not isinstance(channel, discord.abc.Messageable):
            logger.warning("level feed channel unavailable or invalid")
            return

        xp_gain = max(new_xp - old_xp, 0)
        embed = discord.Embed(
            title="⬆️ LEVEL UP DANS LE REFUGE ! 🎮",
            description=(
                f"🔥 {member.mention} passe **niveau {new_level}**\n"
                f"+{xp_gain} XP – activité détectée 💬⚡\n\n"
                "GG ! Le Refuge te voit 👀"
            ),
            color=discord.Color(0xFF5DA2),
        )
        avatar_url = getattr(getattr(member, "display_avatar", None), "url", None)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("missing permission to send level feed message")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("failed to send level feed message: %s", exc)

    async def close(self) -> None:  # type: ignore[override]
        """Ensure background helpers are stopped before shutting down."""
        await limiter.aclose()
        await api_meter.aclose()
        await rename_manager.aclose()
        # Stop generic fire-and-forget work before closing storage so no
        # checkpoint can outlive the resources it persists.
        await background_tasks.aclose()
        await xp_store.aclose()
        await super().close()


def create_bot() -> RefugeBot:
    """Create a :class:`RefugeBot` with default intents.

    Having a factory function makes it easier for tests and static type
    checkers to create a bot instance without executing side effects at module
    import time.
    """

    intents: discord.Intents = discord.Intents(
        guilds=True,
        members=True,
        presences=True,
        messages=True,
        reactions=True,
        voice_states=True,
        message_content=True,
    )
    return RefugeBot(command_prefix="!", intents=intents)


__all__ = [
    "RefugeBot",
    "xp_store",
    "rename_manager",
    "api_meter",
    "background_tasks",
    "limiter",
    "reset_http_error_counter",
    "create_bot",
]
