"""Diagnostics et statistiques des appels API."""

from __future__ import annotations

import discord
from discord.ext import commands
from discord import app_commands

from utils.api_meter import api_meter


class APIStatsCog(commands.Cog):
    """Expose les statistiques d'utilisation de l'API."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="api_stats", description="Affiche les statistiques API")
    @app_commands.describe(window="Fenêtre en minutes", top="Nombre d'entrées à afficher")
    async def api_stats(
        self, interaction: discord.Interaction, window: int = 10, top: int = 10
    ) -> None:
        routes = api_meter.get_top_routes(window, top)
        sources = api_meter.get_top_sources(window, top)
        alerts = api_meter.get_active_alerts()

        embed = discord.Embed(
            title=f"API stats ({window} min)", color=discord.Color.blue()
        )
        if routes:
            lines = [
                f"{r['route']} — {r['calls']} calls, {r['429']}×429, {r['avg_ms']:.0f}ms avg, slow={r['slow']}"
                for r in routes
            ]
            embed.add_field(
                name="Top Routes", value="\n".join(lines[:25]), inline=False
            )
        if sources:
            lines = [
                f"{s['source']} — {s['calls']} calls, {s['429']}×429, {s['avg_ms']:.0f}ms avg"
                for s in sources
            ]
            embed.add_field(
                name="Top Sources", value="\n".join(lines[:25]), inline=False
            )
        if alerts:
            embed.add_field(name="Alerts", value="\n".join(alerts[:25]), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(APIStatsCog(bot))
