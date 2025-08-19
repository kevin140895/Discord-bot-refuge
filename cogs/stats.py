"""Mise à jour des salons de statistiques du serveur.

La cog renomme périodiquement les canaux affichant le nombre de
membres, les utilisateurs en ligne et l'activité vocale. Elle ne recourt
à aucune persistance, s'appuyant seulement sur ``rename_manager`` pour
effectuer les changements.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import time

import config
from config import XP_VIEWER_ROLE_ID
from utils.rename_manager import rename_manager
from utils.interactions import safe_respond
from utils.metrics import measure


class StatsCog(commands.Cog):
    """Gestion des salons de statistiques (membres, activité, etc.)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.refresh_members.start()
        self.refresh_activity.start()

    def cog_unload(self) -> None:
        self.refresh_members.cancel()
        self.refresh_activity.cancel()

    async def update_members(self, guild: discord.Guild) -> None:
        """Met à jour le nombre de membres pour ``guild``."""
        with measure("stats.update_members"):
            category = guild.get_channel(config.STATS_CATEGORY_ID)
            if category is None:
                return
            members = sum(1 for m in guild.members if not getattr(m, "bot", False))
            channels = getattr(category, "channels", [])
            if len(channels) > 0:
                await rename_manager.request(
                    channels[0], f"👥 Membres : {members}"
                )

    async def update_online(self, guild: discord.Guild) -> None:
        """Met à jour le nombre d'utilisateurs en ligne pour ``guild``."""
        with measure("stats.update_online"):
            category = guild.get_channel(config.STATS_CATEGORY_ID)
            if category is None:
                return
            online = sum(
                1
                for m in guild.members
                if not getattr(m, "bot", False) and m.status != discord.Status.offline
            )
            channels = getattr(category, "channels", [])
            if len(channels) > 1:
                await rename_manager.request(
                    channels[1], f"🟢 En ligne : {online}"
                )

    async def update_voice(self, guild: discord.Guild) -> None:
        """Met à jour le nombre d'utilisateurs en vocal pour ``guild``."""
        with measure("stats.update_voice"):
            category = guild.get_channel(config.STATS_CATEGORY_ID)
            if category is None:
                return
            voice = sum(
                len([m for m in vc.members if not getattr(m, "bot", False)])
                for vc in getattr(guild, "voice_channels", [])
            )
            channels = getattr(category, "channels", [])
            if len(channels) > 2:
                await rename_manager.request(
                    channels[2], f"🔊 Voc : {voice}"
                )

    @tasks.loop(time=[time(hour=10), time(hour=22)])
    async def refresh_members(self) -> None:
        """Met à jour le nombre de membres deux fois par jour."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self.update_members(guild)

    @tasks.loop(minutes=10)
    async def refresh_activity(self) -> None:
        """Met à jour l'activité du serveur toutes les dix minutes."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self.update_online(guild)
            await self.update_voice(guild)

    @app_commands.command(name="stats_refresh", description="Met à jour les salons de statistiques.")
    async def slash_stats_refresh(self, interaction: discord.Interaction) -> None:
        if not any(r.id == XP_VIEWER_ROLE_ID for r in interaction.user.roles):
            await safe_respond(interaction, "Accès refusé.", ephemeral=True)
            return
        with measure("slash:stats_refresh"):
            await self.update_members(interaction.guild)
            await self.update_online(interaction.guild)
            await self.update_voice(interaction.guild)
            await safe_respond(interaction, "Statistiques mises à jour", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
