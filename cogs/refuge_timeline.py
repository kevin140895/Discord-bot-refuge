from __future__ import annotations

import asyncio
import logging

from discord.ext import commands, tasks

from services.refuge_timeline import refuge_timeline_service


logger = logging.getLogger(__name__)


class RefugeTimelineCog(commands.Cog):
    """Keep monthly Refuge chapters synchronized with Paris calendar months."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            await refuge_timeline_service.sync()
        except Exception:
            logger.exception("[refuge] initialisation de la Chronologie échouée")
        if not self.timeline_rollover.is_running():
            self.timeline_rollover.start()

    def cog_unload(self) -> None:
        self.timeline_rollover.cancel()

    @tasks.loop(minutes=1)
    async def timeline_rollover(self) -> None:
        try:
            await refuge_timeline_service.sync()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[refuge] rollover de Chronologie échoué")

    @timeline_rollover.before_loop
    async def before_timeline_rollover(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefugeTimelineCog(bot))


__all__ = ["RefugeTimelineCog"]
