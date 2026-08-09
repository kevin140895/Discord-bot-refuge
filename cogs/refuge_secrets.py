from __future__ import annotations

import asyncio
import logging

from discord.ext import commands, tasks

from services.refuge_secrets import refuge_secrets_service


logger = logging.getLogger(__name__)


class RefugeSecretsCog(commands.Cog):
    """Discover hidden Refuge events from real community evidence."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # The first sync only establishes REFUGE-012 prospective activation.
        try:
            await refuge_secrets_service.sync()
        except Exception:
            logger.exception("[refuge] initialisation des événements secrets échouée")
        if not self.discover_hidden_events.is_running():
            self.discover_hidden_events.start()

    def cog_unload(self) -> None:
        self.discover_hidden_events.cancel()

    @tasks.loop(minutes=1)
    async def discover_hidden_events(self) -> None:
        try:
            result = await refuge_secrets_service.sync()
            for discovery in result.discoveries:
                logger.info(
                    "[refuge] découverte persistée: building=%s marker=%s",
                    discovery.building_id,
                    discovery.marker_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[refuge] détection des événements secrets échouée")

    @discover_hidden_events.before_loop
    async def before_discover_hidden_events(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefugeSecretsCog(bot))


__all__ = ["RefugeSecretsCog"]
