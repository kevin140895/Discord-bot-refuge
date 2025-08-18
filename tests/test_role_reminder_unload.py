import asyncio
from pathlib import Path
import sys

import pytest
import discord
from discord.ext import commands

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.role_reminder import RoleReminderCog


@pytest.mark.asyncio
async def test_cog_unload_cancels_tasks():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())

    cog = RoleReminderCog(bot)

    await bot.add_cog(cog)
    await bot.remove_cog(cog.__cog_name__)

    await asyncio.sleep(0)

    assert not cog._scan_loop.is_running()
    assert not cog._cleanup_loop.is_running()

    await bot.close()
