from __future__ import annotations

import discord
import pytest
from discord.ext import commands

from cogs.casino_visual_preview import CasinoVisualPreviewCog
from config import GUILD_ID


@pytest.mark.asyncio
async def test_casino_preview_command_is_registered_for_configured_guild():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        await bot.add_cog(CasinoVisualPreviewCog(bot))
        guild = discord.Object(id=GUILD_ID)

        assert bot.tree.get_command("casino_preview_visuel", guild=guild) is not None
        assert bot.tree.get_command("casino_preview_visuel") is None
    finally:
        await bot.close()
