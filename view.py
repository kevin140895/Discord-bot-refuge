import discord

# IDs des rôles
ROLE_PC      = 1400560541529018408
ROLE_CONSOLE = 1400560660710162492


class PlayerTypeView(discord.ui.View):
    """Deux boutons : Console ou PC."""

    def __init__(self):
        super().__init__(timeout=60)      # 60 s pour cliquer

    # ---------- Bouton Console ----------
    @discord.ui.button(label="🎮 Console", style=discord.ButtonStyle.primary)
    async def console_button(self, interaction: discord.Interaction, _):
        await self._assign_role(interaction, ROLE_CONSOLE, "Console")

    # ---------- Bouton PC ----------
    @discord.ui.button(label="💻 PC", style=discord.ButtonStyle.secondary)
    async def pc_button(self, interaction: discord.Interaction, _):
        await self._assign_role(interaction, ROLE_PC, "PC")

    # ---------- Logique commune ----------
    async def _assign_role(self, interaction, role_id: int, label: str):
        guild = interaction.guild
        member = interaction.user

        role = guild.get_role(role_id)
        other_role = guild.get_role(
            ROLE_PC if role_id == ROLE_CONSOLE else ROLE_CONSOLE
        )

        # Ajout / retrait des rôles
        roles = set(member.roles)
        roles.discard(other_role)
        roles.add(role)
        await member.edit(roles=list(roles), reason="Choix type joueur")

        # Confirmation éphémère
        await interaction.response.send_message(
            f"✅ Tu es maintenant classé **{label}** !",
            ephemeral=True
        )

        # Suppression du message public (nécessite Manage Messages)
        try:
            await interaction.message.delete()
        except discord.Forbidden:
            # On ignore si le bot n'a pas la permission
            pass
