from __future__ import annotations

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import GUILD_ID
from services.casino_visual_cache import casino_visual_cache
from services.refuge_casino import refuge_casino_service


logger = logging.getLogger(__name__)


class CasinoVisualPreviewCog(commands.Cog):
    """Admin-only visual QA surface for the living Casino renderer."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="casino_preview_visuel",
        description="Prévisualiser un état visuel du Casino du Refuge",
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        phase="Moment de la journée à simuler",
        fortune="Fortune de la Maison à simuler",
        etat="Ouverture ou fermeture à simuler",
        reaction="Réaction visuelle Lot 4 à simuler",
    )
    @app_commands.choices(
        phase=[
            app_commands.Choice(name="Aube", value="dawn"),
            app_commands.Choice(name="Journée", value="day"),
            app_commands.Choice(name="Heure dorée", value="golden"),
            app_commands.Choice(name="Crépuscule", value="dusk"),
            app_commands.Choice(name="Nuit", value="night"),
            app_commands.Choice(name="Fin de nuit", value="late_night"),
        ],
        fortune=[
            app_commands.Choice(name="Ruiné", value="ruined"),
            app_commands.Choice(name="En difficulté", value="difficulty"),
            app_commands.Choice(name="Stable", value="stable"),
            app_commands.Choice(name="Prospère", value="prosperous"),
            app_commands.Choice(name="Insolent", value="insolent"),
        ],
        etat=[
            app_commands.Choice(name="Ouvert", value="open"),
            app_commands.Choice(name="Fermé", value="closed"),
        ],
        reaction=[
            app_commands.Choice(name="Normal", value="normal"),
            app_commands.Choice(name="Tables actives", value="active"),
            app_commands.Choice(name="Forte affluence", value="busy"),
            app_commands.Choice(name="Zéro Vert", value="green_zero"),
            app_commands.Choice(name="Gros gain royal", value="royal_win"),
            app_commands.Choice(name="Série joueurs", value="players_streak"),
            app_commands.Choice(name="Série Maison", value="house_streak"),
        ],
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def casino_preview_visuel(
        self,
        interaction: discord.Interaction,
        phase: app_commands.Choice[str],
        fortune: app_commands.Choice[str],
        etat: app_commands.Choice[str],
        reaction: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            status = await refuge_casino_service.evaluate()
            reaction_value = reaction.value if reaction is not None else "normal"
            reaction_name = reaction.name if reaction is not None else "Normal"
            asset = await casino_visual_cache.get_or_render(
                status,
                phase_override=phase.value,
                fortune_override=fortune.value,
                open_override=etat.value == "open",
                reaction_override=reaction_value,
            )
            payload = await asset.read_bytes()
            file = discord.File(io.BytesIO(payload), filename="casino-preview.png")
            cache_label = "cache" if asset.cache_hit else "nouveau rendu"
            await interaction.followup.send(
                (
                    f"🎨 **Prévisualisation Casino** · {phase.name} · "
                    f"{fortune.name} · {etat.name} · {reaction_name}\n"
                    f"-# {cache_label} · aucun réglage économique n'a été modifié."
                ),
                file=file,
                ephemeral=True,
            )
        except Exception:
            logger.exception("[CasinoVisual] génération de la prévisualisation échouée")
            await interaction.followup.send(
                "❌ Impossible de générer cette prévisualisation pour le moment.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CasinoVisualPreviewCog(bot))


__all__ = ["CasinoVisualPreviewCog"]
