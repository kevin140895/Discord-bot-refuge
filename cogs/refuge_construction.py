from __future__ import annotations

import asyncio
import logging

from discord.ext import commands, tasks

from services.refuge_construction import refuge_construction_service


logger = logging.getLogger(__name__)


class RefugeConstructionCog(commands.Cog):
    """Reconcile completed community goals and advance the active chantier."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # This first sync establishes the prospective REFUGE-010 activation marker
        # before future community goals can become eligible build rights.
        try:
            await refuge_construction_service.sync()
        except Exception:
            logger.exception("[refuge] initialisation du Chantier échouée")
        if not self.advance_construction.is_running():
            self.advance_construction.start()

    def cog_unload(self) -> None:
        self.advance_construction.cancel()

    @tasks.loop(minutes=1)
    async def advance_construction(self) -> None:
        try:
            await refuge_construction_service.sync()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[refuge] avancement du Chantier échoué")

    @advance_construction.before_loop
    async def before_advance_construction(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefugeConstructionCog(bot))


__all__ = ["RefugeConstructionCog"]
