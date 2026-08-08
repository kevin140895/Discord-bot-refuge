from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services.member_profile import member_profile_service
from ui.profile_view import ProfileView


logger = logging.getLogger(__name__)


class ProfileCog(commands.Cog):
    """Expose the Refuge member profile as a Components V2 layout."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="profil",
        description="Affiche le profil Refuge d'un membre",
    )
    @app_commands.describe(membre="Membre à consulter (toi par défaut)")
    async def profil(
        self,
        interaction: discord.Interaction,
        membre: discord.Member | None = None,
    ) -> None:
        target = membre
        if target is None and isinstance(interaction.user, discord.Member):
            target = interaction.user
        if target is None and interaction.guild is not None:
            target = interaction.guild.get_member(interaction.user.id)

        if target is None:
            await interaction.response.send_message(
                "Impossible de déterminer le membre à afficher.",
                ephemeral=True,
            )
            return

        try:
            snapshot = await member_profile_service.get_snapshot(target.id)
        except Exception:
            logger.exception("failed to build profile for user %s", target.id)
            await interaction.response.send_message(
                "Impossible de charger ce profil pour le moment.",
                ephemeral=True,
            )
            return

        avatar = getattr(target, "display_avatar", None)
        avatar_url = str(avatar.url) if getattr(avatar, "url", None) else None
        view = ProfileView(
            snapshot,
            display_name=target.display_name,
            avatar_url=avatar_url,
        )
        await interaction.response.send_message(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot))
