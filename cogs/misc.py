import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.interactions import safe_respond
from view import PlayerTypeView
from config import CHANNEL_ROLES, OWNER_ID


class MiscCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="type_joueur", description="Choisir PC, Console ou Mobile")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def type_joueur(self, interaction: discord.Interaction) -> None:
        await safe_respond(
            interaction,
            f"Les boutons ont été postés dans <#{CHANNEL_ROLES}> 😉",
            ephemeral=True,
        )
        channel = interaction.guild.get_channel(CHANNEL_ROLES)
        if channel:
            await channel.send("Quel type de joueur es-tu ?", view=PlayerTypeView())

    @app_commands.command(name="sondage", description="Créer un sondage Oui/Non")
    @app_commands.describe(question="La question à poser")
    async def sondage(self, interaction: discord.Interaction, question: str) -> None:
        msg = await interaction.channel.send(
            f"📊 **{question}**\n> ✅ = Oui   ❌ = Non\n_Posé par {interaction.user.mention}_"
        )
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await safe_respond(interaction, "Sondage créé ✔️", ephemeral=True)

    @app_commands.command(name="lien", description="Affiche le lien pour rejoindre le serveur Discord")
    async def lien(self, interaction: discord.Interaction) -> None:
        await safe_respond(
            interaction,
            "🔗 Voici le lien pour rejoindre notre serveur :\nhttps://discord.com/invite/lerefuge57",
            ephemeral=False,
        )

    @app_commands.command(name="purge", description="Supprime N messages récents de ce salon (réservé à Kevin)")
    @app_commands.describe(nb="Nombre de messages à supprimer (1-100)")
    async def purge(self, interaction: discord.Interaction, nb: app_commands.Range[int, 1, 100]) -> None:
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)
        except Exception as e:
            logging.debug("purge defer failed: %s", e)
        if interaction.user.id != OWNER_ID:
            await interaction.followup.send("❌ Commande réservée au propriétaire.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.followup.send("❌ Utilisable uniquement sur un serveur.", ephemeral=True)
            return
        ch = interaction.channel
        if ch is None:
            await interaction.followup.send("❌ Salon introuvable.", ephemeral=True)
            return
        me = interaction.guild.me or interaction.guild.get_member(self.bot.user.id)
        if not me:
            await interaction.followup.send("❌ Impossible de vérifier mes permissions.", ephemeral=True)
            return
        perms = ch.permissions_for(me)
        if not perms.manage_messages or not perms.read_message_history:
            await interaction.followup.send(
                "❌ Il me manque les permissions **Gérer les messages** et/ou **Lire l’historique**.",
                ephemeral=True,
            )
            return
        try:
            if isinstance(ch, discord.TextChannel):
                deleted = await ch.purge(limit=nb, check=lambda m: not m.pinned, bulk=True)
                await interaction.followup.send(
                    f"🧹 {len(deleted)} messages supprimés.", ephemeral=True
                )
                return
        except Exception as e:
            logging.warning(f"Purge bulk échouée, fallback lent. Raison: {e}")
        count = 0
        try:
            async for msg in ch.history(limit=nb):
                if msg.pinned:
                    continue
                try:
                    await msg.delete()
                    count += 1
                except Exception as ee:
                    logging.error(f"Suppression d'un message échouée: {ee}")
            await interaction.followup.send(
                f"🧹 {count} messages supprimés (mode lent).", ephemeral=True
            )
        except Exception as ee:
            logging.error(f"Erreur lors de la purge lente: {ee}")
            await interaction.followup.send(
                "❌ Impossible de supprimer les messages.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MiscCog(bot))
