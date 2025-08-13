import os
import logging
import discord
from discord.ext import commands
from discord import app_commands

# On utilisera ces modules à l'étape 5
from utils.timewin import is_open_now, next_boundary_dt  # déjà ok dans le squelette
from storage.roulette_store import RouletteStore          # squelette pour persistance

PARIS_TZ = "Europe/Paris"

# Placeholders: seront fournis à l'Étape 4
ROLE_ID: int | None = None        # <- on demandera ton ROLE_ID ici
CHANNEL_ID: int | None = None     # <- on demandera ton CHANNEL_ID ici

class RouletteView(discord.ui.View):
    """Vue persistante avec le bouton 🎰 Roulette."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎰 Roulette",
        style=discord.ButtonStyle.success,
        custom_id="roulette:play"  # custom_id stable pour vue persistante
    )
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Étape 5 : on mettra toute la logique ici (fenêtre horaire, tirage, XP, rôle…)
        await interaction.response.send_message(
            "🛠️ La roulette arrive bientôt (squelette en place).", ephemeral=True
        )

class RouletteCog(commands.Cog):
    """Cog 'Roulette' — squelette. Implémentation complète à l'étape 5."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        data_dir = os.getenv("DATA_DIR", "/app/data")
        self.store = RouletteStore(data_dir=data_dir)
        self.view = RouletteView()

    async def cog_load(self):
        """Enregistre la vue persistante au chargement du cog."""
        try:
            self.bot.add_view(self.view)  # pour les messages existants (persistant)
        except Exception as e:
            logging.error(f"[Roulette] add_view échoué: {e}")

    # ───────────────────────── SLASH COMMANDS (squelettes) ─────────────────────────

    @app_commands.command(name="roulette-poster", description="Publie le message Roulette avec le bouton (squelette)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roulette_poster(self, interaction: discord.Interaction):
        # Étape 5 : on postera l'embed + bouton dans CHANNEL_ID et on mémorisera (channel_id, message_id)
        ch = interaction.guild.get_channel(CHANNEL_ID) if CHANNEL_ID else interaction.channel
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("❌ Salon cible introuvable.", ephemeral=True)

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        embed = discord.Embed(
            title="🎰 Roulette — Bientôt disponible",
            description="Le bouton est en place, la logique sera activée à l’étape 5.",
            color=0x2ECC71
        )
        try:
            msg = await ch.send(embed=embed, view=self.view)
            await interaction.followup.send(f"✅ Message posté dans <#{ch.id}> (id: {msg.id}).", ephemeral=True)
        except Exception as e:
            logging.error(f"[Roulette] Poster échoué: {e}")
            await interaction.followup.send("❌ Impossible de poster le message.", ephemeral=True)

    @app_commands.command(name="roulette-reset-user", description="Réinitialise l’état Roulette d’un membre (squelette)")
    @app_commands.describe(membre="Membre à réinitialiser")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def roulette_reset_user(self, interaction: discord.Interaction, membre: discord.Member):
        # Étape 5 : on supprimera la marque 'déjà utilisé' + éventuelles entrées de rôle
        await interaction.response.send_message(
            f"🧪 Squelette : je réinitialiserai {membre.mention} à l’étape 5.", ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(RouletteCog(bot))
