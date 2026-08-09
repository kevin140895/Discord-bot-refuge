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
        self.profile_context_menu = app_commands.ContextMenu(
            name="Profil du Refuge",
            callback=self.profile_context_menu_callback,
        )
        self.bot.tree.add_command(self.profile_context_menu)

    def cog_unload(self) -> None:
        """Remove the manually registered context menu when the cog unloads."""

        self.bot.tree.remove_command(
            self.profile_context_menu.name,
            type=self.profile_context_menu.type,
        )

    async def _send_profile(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
    ) -> None:
        """Build and send the same profile view for every entry point."""

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
            owner_user_id=interaction.user.id,
        )
        await interaction.response.send_message(view=view)

    async def profile_context_menu_callback(
        self,
        interaction: discord.Interaction,
        membre: discord.Member,
    ) -> None:
        """Open the Refuge profile from Discord's member Apps menu."""

        await self._send_profile(interaction, membre)

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

        await self._send_profile(interaction, target)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot))
