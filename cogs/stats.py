import discord
from discord.ext import commands, tasks
from discord import app_commands

import config
from utils.discord_utils import safe_channel_edit
from utils.interactions import safe_respond


class StatsCog(commands.Cog):
    """Gestion des salons de statistiques (membres, activité, etc.)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.refresh_stats.start()

    def cog_unload(self) -> None:
        self.refresh_stats.cancel()

    async def update_stats(self, guild: discord.Guild) -> None:
        """Met à jour les salons de statistiques pour ``guild``."""
        category = guild.get_channel(config.STATS_CATEGORY_ID)
        if category is None:
            return
        members = sum(1 for m in guild.members if not getattr(m, "bot", False))
        online = sum(
            1
            for m in guild.members
            if not getattr(m, "bot", False) and m.status != discord.Status.offline
        )
        voice = sum(
            len([m for m in vc.members if not getattr(m, "bot", False)])
            for vc in getattr(guild, "voice_channels", [])
        )
        channels = getattr(category, "channels", [])
        if len(channels) > 0:
            await safe_channel_edit(channels[0], name=f"👥 Membres : {members}")
        if len(channels) > 1:
            await safe_channel_edit(channels[1], name=f"🟢 En ligne : {online}")
        if len(channels) > 2:
            await safe_channel_edit(channels[2], name=f"🔊 Voc : {voice}")

    @tasks.loop(minutes=10)
    async def refresh_stats(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self.update_stats(guild)

    @app_commands.command(name="stats_refresh", description="Met à jour les salons de statistiques.")
    async def slash_stats_refresh(self, interaction: discord.Interaction) -> None:
        await self.update_stats(interaction.guild)
        await safe_respond(interaction, "Statistiques mises à jour", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
