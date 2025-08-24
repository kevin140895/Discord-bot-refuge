"""Main bot implementation for tests.

This module provides a minimal :class:`RefugeBot` used in the test
suite.  It exposes a few global helpers (``xp_store``
``rename_manager`` etc.) so tests can monkeypatch them and verify
interaction with the bot.
"""

from __future__ import annotations

from discord.ext import commands

from storage.xp_store import xp_store
from utils.api_meter import api_meter
from utils.channel_edit_manager import channel_edit_manager
from utils.rename_manager import rename_manager
from utils.rate_limit import GlobalRateLimiter
from view import PlayerTypeView

# global rate limiter instance
limiter = GlobalRateLimiter()

async def reset_http_error_counter() -> None:
    """Reset the HTTP error counter (placeholder)."""
    # Real implementation would reset metrics.  In tests this coroutine is
    # monkeypatched, so the body can stay empty.
    return None


class RefugeBot(commands.Bot):
    """Discord bot with minimal startup and shutdown logic for tests."""

    async def setup_hook(self) -> None:  # type: ignore[override]
        """Start background helpers and synchronise the command tree."""
        # Start background helpers. In the real project these are asynchronous
        # coroutines, hence we ``await`` them so the test suite can verify
        # they have been invoked.
        await xp_store.start()
        await rename_manager.start()
        await channel_edit_manager.start()
        await api_meter.start(self)
        limiter.start()
        await reset_http_error_counter()

        # Load Machine à sous cog.  ``load_extension`` is patched to an
        # ``AsyncMock`` in the tests, so awaiting is safe.
        await self.load_extension("cogs.machine_a_sous")
        await self.tree.sync()

        # Register persistent views. ``add_view`` can only be called once per
        # view instance; protect against duplicates when ``setup_hook`` runs
        # multiple times during tests or restarts.
        if not getattr(self, "_player_type_view_added", False):
            self.add_view(PlayerTypeView())
            self._player_type_view_added = True

    async def close(self) -> None:  # type: ignore[override]
        """Ensure background helpers are stopped before shutting down."""
        await rename_manager.aclose()
        await channel_edit_manager.aclose()
        await xp_store.aclose()
        await super().close()


__all__ = [
    "RefugeBot",
    "xp_store",
    "rename_manager",
    "channel_edit_manager",
    "api_meter",
    "limiter",
    "reset_http_error_counter",
]

