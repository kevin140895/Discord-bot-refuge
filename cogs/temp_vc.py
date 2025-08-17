import asyncio
import logging
from typing import Set

import discord
from discord.ext import commands, tasks

from utils.temp_vc_cleanup import delete_untracked_temp_vcs
from storage.temp_vc_store import load_temp_vc_ids, save_temp_vc_ids
from config import TEMP_VC_CATEGORY

# IDs des salons vocaux temporaires connus
TEMP_VC_IDS: Set[int] = set(load_temp_vc_ids())


class TempVCCog(commands.Cog):
    """Maintenance des salons vocaux temporaires."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cleanup.start()

    def cog_unload(self) -> None:
        self.cleanup.cancel()

    @tasks.loop(minutes=10)
    async def cleanup(self) -> None:
        await delete_untracked_temp_vcs(self.bot, TEMP_VC_CATEGORY, TEMP_VC_IDS)
        save_temp_vc_ids(TEMP_VC_IDS)

    @cleanup.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TempVCCog(bot))
