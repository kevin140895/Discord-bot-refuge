import os
import re
import logging
import asyncio

import discord
from discord.ext import commands
from discord import app_commands, File, Embed
from dotenv import load_dotenv

from view import PlayerTypeView, ROLE_PC, ROLE_CONSOLE  # votre view.py

# ── Chargement du token et constantes ────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ROLES        = 1400560866478395512  # #choix-de-rôles
CHANNEL_WELCOME      = 1400550333796716574  # #bienvenue
REMINDER_INTERVAL_H  = 24                   # heures entre chaque rappel

WELCOME_TEXT = (
    "🎉 Bienvenue {member}! Tu viens d’entrer au Refuge : "
    "prends un rô-lé 🕹️ dans #choix-de-rôles et installe-toi."
)

# ── Salons vocaux temporaires ────────────────────────────────────────────
LOBBY_TEXT_CHANNEL = 1402258805533970472  # salon où poster les boutons VC
TEMP_VC_CATEGORY   = 1400559884117999687  # catégorie "Salons Vocaux"

VC_PROFILES = {
    "PC":        {"emoji": "💻"},
    "Crossplay": {"emoji": "🔀"},
    "Consoles":  {"emoji": "🎮"},
}
VOC_PATTERN = re.compile(r"^(PC|Crossplay|Consoles)(?: (\d+))?$", re.I)
TEMP_VC_IDS: set[int] = set()

# ── Configuration du logging ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ── Intents ───────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True  # pour détecter les sorties de VC

bot = commands.Bot(command_prefix="!", intents=intents)


# ── Fonctions utilitaires pour salons vocaux ─────────────────────────────
def next_vc_name(guild: discord.Guild, base: str) -> str:
    """
    Génère un nom unique pour un salon vocal temporaire
    (e.g. "PC", "PC 2", "PC 3", ...).
    """
    existing = [
        int(m.group(2))
        for ch in guild.voice_channels
        if (m := VOC_PATTERN.match(ch.name)) and m.group(1).lower() == base.lower()
    ]
    n = max(existing) + 1 if existing else 1
    return base if n == 1 else f"{base} {n}"


class VCButtonView(discord.ui.View):
    """Vue persistante avec 3 boutons pour créer des salons vocaux."""

    def __init__(self):
        super().__init__(timeout=None)

    async def create_vc(self, interaction: discord.Interaction, profile: str):
        guild = interaction.guild
        category = guild.get_channel(TEMP_VC_CATEGORY)
        if category is None:
            await interaction.response.send_message(
                "⚠️ Catégorie vocaux introuvable !", ephemeral=True
            )
            return

        name = next_vc_name(guild, profile)
        emoji = VC_PROFILES[profile]["emoji"]
        channel = await guild.create_voice_channel(
            name=f"{emoji} {name}",
            category=category,
            user_limit=5,
            reason=f"Salon temporaire créé par {interaction.user}"
        )
        TEMP_VC_IDS.add(channel.id)

        if interaction.user.voice:
            await interaction.user.move_to(channel)

        await interaction.response.send_message(
            f"✅ Salon **{name}** créé. Il disparaîtra quand il sera vide !",
            ephemeral=True
        )

    @discord.ui.button(label="PC",        emoji="💻", style=discord.ButtonStyle.blurple, custom_id="vc_pc")
    async def pc(self, interaction: discord.Interaction, _):
        await self.create_vc(interaction, "PC")

    @discord.ui.button(label="Crossplay", emoji="🔀", style=discord.ButtonStyle.blurple, custom_id="vc_cross")
    async def cross(self, interaction: discord.Interaction, _):
        await self.create_vc(interaction, "Crossplay")

    @discord.ui.button(label="Consoles",  emoji="🎮", style=discord.ButtonStyle.blurple, custom_id="vc_console")
    async def consoles(self, interaction: discord.Interaction, _):
        await self.create_vc(interaction, "Consoles")


# ── Commandes slash ───────────────────────────────────────────────────────
@bot.tree.command(name="type_joueur", description="Choisir PC ou Console")
@app_commands.checks.has_permissions(manage_guild=True)
async def type_joueur(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Les boutons ont été postés dans <#{CHANNEL_ROLES}> 😉",
        ephemeral=True
    )
    channel = interaction.guild.get_channel(CHANNEL_ROLES)
    if channel:
        await channel.send("Quel type de joueur es-tu ?", view=PlayerTypeView())
    else:
        logging.warning("CHANNEL_ROLES introuvable")


@bot.tree.command(name="sondage", description="Créer un sondage Oui/Non")
@app_commands.describe(question="La question à poser")
async def sondage(interaction: discord.Interaction, question: str):
    msg = await interaction.channel.send(
        f"📊 **{question}**\n"
        f"> ✅ = Oui   ❌ = Non\n"
        f"_Posé par {interaction.user.mention}_"
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await interaction.response.send_message("Sondage créé ✔️", ephemeral=True)


# ── Accueil et rappels ────────────────────────────────────────────────────
async def _send_welcome(member: discord.Member):
    channel = member.guild.get_channel(CHANNEL_WELCOME)
    if channel is None:
        logging.warning("Salon bienvenue introuvable")
        return

    embed = Embed(
        title="Bienvenue au Refuge !",
        description="Installe-toi, choisis ton rôle et have fun 🎮",
        colour=0x3498db
    ).set_thumbnail(url="attachment://logo.png")
    file = File("logo.png", filename="logo.png")

    await channel.send(
        content=WELCOME_TEXT.format(member=member.mention),
        embed=embed,
        file=file
    )


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    if any(role.id in (ROLE_PC, ROLE_CONSOLE) for role in member.roles):
        return
    await _send_welcome(member)


async def _send_role_reminders():
    for guild in bot.guilds:
        channel = guild.get_channel(CHANNEL_ROLES)
        if channel is None:
            continue
        for member in guild.members:
            if member.bot:
                continue
            if any(role.id in (ROLE_PC, ROLE_CONSOLE) for role in member.roles):
                continue
            await channel.send(
                f"Hey {member.mention} — choisis ton rôle 👇",
                view=PlayerTypeView()
            )
            await asyncio.sleep(1)  # pour éviter le flood


async def _reminder_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await _send_role_reminders()
        except Exception:
            logging.exception("Erreur lors du rappel automatique :")
        await asyncio.sleep(REMINDER_INTERVAL_H * 3600)


# ── Gestion des salons vocaux temporaires ────────────────────────────────
@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    if before.channel and before.channel.id in TEMP_VC_IDS and not before.channel.members:
        try:
            await before.channel.delete(reason="Salon temporaire vide")
        finally:
            TEMP_VC_IDS.discard(before.channel.id)


# ── Démarrage, sync & enregistrement des vues ───────────────────────────
async def _setup_hook():
    await bot.tree.sync()
    asyncio.create_task(_reminder_loop())

bot.setup_hook = _setup_hook


@bot.event
async def on_ready():
    logging.info(f"Connecté : {bot.user} (id={bot.user.id})")
    bot.add_view(VCButtonView())

    lobby = bot.get_channel(LOBBY_TEXT_CHANNEL)
    if lobby is None:
        logging.warning("Salon lobby introuvable")
        return

    # Vérifier si un message existe déjà avec les composants
    async for msg in lobby.history(limit=50):
        if msg.author == bot.user and msg.components:
            break
    else:
        await lobby.send(
            "__**Crée ton salon vocal temporaire :**__",
            view=VCButtonView()
        )


# ── Lancement du bot ─────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
