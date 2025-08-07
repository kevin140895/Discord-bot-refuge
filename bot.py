import os
import re
import json
import random
import logging
import asyncio
from pathlib import Path

import discord
from discord import app_commands, Embed
from discord.ext import commands
from discord.ui import Button, View

from dotenv import load_dotenv
from view import PlayerTypeView

from datetime import datetime

voice_times = {}  # user_id: datetime d'entrée

# ─────────────────────── SAUVEGARDE AUTOMATIQUE XP ───────────
async def auto_backup_xp(interval_seconds=3600):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            source = Path("data/data.json")
            backup = Path("data/backup.json")
            if source.exists():
                backup.write_text(source.read_text())
                logging.info("💾 Sauvegarde automatique effectuée.")
        except Exception as e:
            logging.error(f"❌ Erreur lors de la sauvegarde automatique : {e}")
        await asyncio.sleep(interval_seconds)


# ─────────────────────── CONFIGURATION ──────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

XP_FILE = "data/data.json"

LEVEL_UP_CHANNEL = 1402419913716531352
CHANNEL_ROLES = 1400560866478395512
CHANNEL_WELCOME = 1400550333796716574
LOBBY_TEXT_CHANNEL = 1402258805533970472
TEMP_VC_CATEGORY = 1400559884117999687

VC_PROFILES = {
    "PC": {"emoji": "💻"},
    "Crossplay": {"emoji": "🔀"},
    "Consoles": {"emoji": "🎮"},
}

VOC_PATTERN = re.compile(r"^(PC|Crossplay|Consoles)(?: (\d+))?$", re.I)
TEMP_VC_IDS: set[int] = set()

# ─────────────────────── LOGGER ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ─────────────────────── INTENTS ─────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────── XP SYSTEM ───────────────────────
def load_xp():
    path = Path(XP_FILE)
    backup_path = Path("data/backup.json")

    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
            logging.info("📁 Fichier XP manquant, créé automatiquement.")
            return {}

        with path.open("r") as f:
            return json.load(f)

    except json.JSONDecodeError:
        logging.warning("⚠️ Fichier XP corrompu ! Tentative de restauration depuis backup.json...")

        if backup_path.exists():
            try:
                with backup_path.open("r") as b:
                    data = json.load(b)
                    # Sauvegarde le backup comme fichier principal
                    path.write_text(json.dumps(data, indent=4))
                    logging.info("✅ Restauration réussie depuis backup.json.")
                    return data
            except Exception as e:
                logging.error(f"❌ Impossible de lire le backup : {e}")
                return {}
        else:
            logging.error("❌ Aucun backup disponible pour restaurer.")
            return {}

# ─────────────────────── SALONS VOCAUX TEMPORAIRES ────────
def next_vc_name(guild: discord.Guild, base: str) -> str:
    existing = [
        int(m.group(2))
        for ch in guild.voice_channels
        if (m := VOC_PATTERN.match(ch.name)) and m.group(1).lower() == base.lower()
    ]
    n = max(existing) + 1 if existing else 1
    return base if n == 1 else f"{base} {n}"

class LiveTikTokView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔴 Annoncer le live TikTok", style=discord.ButtonStyle.danger, custom_id="announce_live")
    async def announce_live(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = bot.get_channel(1400552164979507263)
        if channel:
            await channel.send(
                "🚨 Kevin est en LIVE sur TikTok !\n🔴 Rejoins maintenant : https://www.tiktok.com/@kevinlerefuge"
            )
            await interaction.response.send_message("✅ Le live a été annoncé !", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Salon cible introuvable.", ephemeral=True)

class VCButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💻 PC", style=discord.ButtonStyle.primary, custom_id="vc_pc")
    async def pc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_vc(interaction, "PC")

    @discord.ui.button(label="🔀 Crossplay", style=discord.ButtonStyle.primary, custom_id="vc_crossplay")
    async def crossplay_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_vc(interaction, "Crossplay")

    @discord.ui.button(label="🎮 Consoles", style=discord.ButtonStyle.primary, custom_id="vc_consoles")
    async def consoles_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_vc(interaction, "Consoles")

    async def create_vc(self, interaction: discord.Interaction, profile: str):
        guild = interaction.guild
        category = guild.get_channel(TEMP_VC_CATEGORY)
        if category is None:
            await interaction.response.send_message("⚠️ Catégorie vocaux introuvable !", ephemeral=True)
            return

        name = next_vc_name(guild, profile)
        emoji = VC_PROFILES[profile]["emoji"]
        try:
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
        except Exception as e:
            logging.error(f"❌ Erreur création salon vocal temporaire : {e}")
            await interaction.response.send_message("❌ Une erreur est survenue.", ephemeral=True)

# ─────────────────────── COMMANDES SLASH ──────────────────
@bot.tree.command(name="clear", description="Supprimer plusieurs messages dans un salon")
@app_commands.describe(amount="Nombre de messages à supprimer (max 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    if amount > 100:
        await interaction.response.send_message("❌ Maximum 100 messages à la fois.", ephemeral=True)
        return

    deleted = await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"🧹 {len(deleted)} messages supprimés.", ephemeral=True)
    
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

@bot.tree.command(name="sondage", description="Créer un sondage Oui/Non")
@app_commands.describe(question="La question à poser")
async def sondage(interaction: discord.Interaction, question: str):
    msg = await interaction.channel.send(
        f"📊 **{question}**\n> ✅ = Oui   ❌ = Non\n_Posé par {interaction.user.mention}_"
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await interaction.response.send_message("Sondage créé ✔️", ephemeral=True)

@bot.tree.command(name="liendiscord", description="Affiche le lien pour rejoindre le serveur Discord")
async def liendiscord(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🔗 Voici le lien pour rejoindre notre serveur :\nhttps://discord.gg/vaJeReXM",
        ephemeral=False
    )

@bot.tree.command(name="rang", description="Affiche ton niveau actuel")
async def rang(interaction: discord.Interaction):
    xp_data = load_xp()
    user_id = str(interaction.user.id)
    if user_id not in xp_data:
        await interaction.response.send_message("Tu n'as pas encore de niveau... Commence à discuter !", ephemeral=True)
        return

    data = xp_data[user_id]
    await interaction.response.send_message(
        f"📊 {interaction.user.mention}, tu es niveau {data['level']} avec {data['xp']} XP.",
        ephemeral=True
    )

@bot.tree.command(name="rang_visuel", description="Affiche ton niveau avec une carte graphique")
async def rang_visuel(interaction: discord.Interaction):
    xp_data = load_xp()
    user_id = str(interaction.user.id)

    if user_id not in xp_data:
        await interaction.response.send_message("Tu n'as pas encore de niveau... Commence à discuter !", ephemeral=True)
        return

    data = xp_data[user_id]
    level = data["level"]
    xp = data["xp"]
    xp_next = (level + 1) ** 2 * 100  # formule XP suivante (modifiable)

    image = await generate_rank_card(interaction.user, level, xp, xp_next)
    file = discord.File(fp=image, filename="rank.png")

    await interaction.response.send_message(file=file)

@bot.tree.command(name="sauvegarder", description="Forcer la sauvegarde manuelle des niveaux (admin uniquement)")
async def sauvegarder(interaction: discord.Interaction):
    if interaction.user.id != 541417878314942495:
        await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
        return

    try:
        source = Path("data/data.json")
        backup = Path("data/backup.json")
        if source.exists():
            backup.write_text(source.read_text())
            await interaction.response.send_message("💾 Sauvegarde XP manuelle effectuée avec succès !", ephemeral=True)
            logging.info("💾 Sauvegarde manuelle déclenchée par le propriétaire.")
        else:
            await interaction.response.send_message("⚠️ Aucun fichier de données XP trouvé.", ephemeral=True)
    except Exception as e:
        logging.error(f"❌ Erreur lors de la sauvegarde manuelle : {e}")
        await interaction.response.send_message("❌ Une erreur est survenue lors de la sauvegarde.", ephemeral=True)

@bot.tree.command(name="vocaux", description="Créer un salon vocal temporaire")
@app_commands.checks.has_permissions(manage_channels=True)
async def vocaux(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🎙️ **Crée ton salon vocal temporaire :**",
        view=VCButtonView(),
        ephemeral=False
    )

@bot.event
async def on_message_delete(message: discord.Message):
    # Si le message supprimé vient du bot et est dans le bon salon
    if (
        message.author == bot.user
        and message.channel.id == LOBBY_TEXT_CHANNEL
        and "Crée ton salon vocal temporaire" in message.content
    ):
        logging.warning("⚠️ Le message des boutons vocaux a été supprimé. Réenvoi en cours...")
        await asyncio.sleep(2)  # Petit délai pour éviter les conflits
        await message.channel.send(
            "🎙️ **Crée ton salon vocal temporaire :**",
            view=VCButtonView()
        )
        logging.info("✅ Message recréé automatiquement après suppression.")


# ─────────────────────── GESTION XP PAR MESSAGE ─────────────
def save_xp(data):
    with open(XP_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_level(xp: int) -> int:
    """Calcule le niveau en fonction de l'XP"""
    level = 0
    while xp >= (level + 1) ** 2 * 100:
        level += 1
    return level

def save_xp(data):
    with open(XP_FILE, "w") as f:
        json.dump(data, f, indent=4)

@bot.event
async def on_message(message: discord.Message):
    # Ignorer les messages des bots
    if message.author.bot:
        return

    # Ignorer les messages en DM
    if not message.guild:
        return

    # Charger les données d'XP
    xp_data = load_xp()
    user_id = str(message.author.id)

    # Initialiser l'utilisateur s'il n'existe pas encore
    if user_id not in xp_data:
        xp_data[user_id] = {"xp": 0, "level": 0}

    # Gagner de l'XP aléatoire entre 5 et 10
    gained_xp = random.randint(5, 10)
    xp_data[user_id]["xp"] += gained_xp

    # Calcul du nouveau niveau
    old_level = xp_data[user_id]["level"]
    new_level = get_level(xp_data[user_id]["xp"])

    # Si l'utilisateur monte de niveau
    if new_level > old_level:
        xp_data[user_id]["level"] = new_level
        try:
            channel = message.guild.get_channel(LEVEL_UP_CHANNEL)
            if channel:
                xp = xp_data[user_id]["xp"]
                xp_needed = (new_level + 1) ** 2 * 100  # Formule pour niveau suivant

                # Générer la carte de niveau visuelle
                image = await generate_rank_card(message.author, new_level, xp, xp_needed)
                file = discord.File(fp=image, filename="level_up.png")

                await channel.send(
                    content=f"🎉 {message.author.mention} est passé **niveau {new_level}** !",
                    file=file
                )
        except Exception as e:
            logging.error(f"Erreur lors de l'envoi de la carte de niveau : {e}")

    # Sauvegarder les données mises à jour
    save_xp(xp_data)

    # Nécessaire pour exécuter les commandes
    await bot.process_commands(message)

# ─────────────────────── MESSAGE DE BIENVENUE ───────────────
@bot.event
async def on_member_join(member: discord.Member):
    channel = bot.get_channel(CHANNEL_WELCOME)
    if not channel:
        logging.warning("❌ Salon de bienvenue introuvable.")
        return

    embed = discord.Embed(
        title="🎉 Bienvenue au Refuge !",
        description=(
            f"{member.mention}, installe-toi bien !\n"
            f"🕹️ Choisis ton rôle dans <#{CHANNEL_ROLES}> pour accéder à toutes les sections.\n"
            f"Ravi de t’avoir parmi nous 🎮"
        ),
        color=0x00ffcc
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Membre #{len(member.guild.members)}")

    try:
        await channel.send(embed=embed)
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi du message de bienvenue : {e}")

# ─────────────────────── RÉPÉTITION 24H : VÉRIF RÔLES ──────
async def _reminder_loop():
    await bot.wait_until_ready()
    guild = discord.utils.get(bot.guilds)
    channel = bot.get_channel(CHANNEL_ROLES)

    if guild is None or channel is None:
        logging.warning("❌ Serveur ou salon de rôles introuvable.")
        return

    while not bot.is_closed():
        logging.info("🔁 Vérification des membres sans rôle...")
        for member in guild.members:
            if member.bot:
                continue
            if len(member.roles) <= 1:
                try:
                    await channel.send(
                        f"{member.mention} tu n’as pas encore choisi ton rôle ici. "
                        "Clique sur un bouton pour sélectionner ta plateforme 🎮💻"
                    )
                except Exception as e:
                    logging.error(f"Erreur en envoyant un rappel à {member.display_name}: {e}")
        await asyncio.sleep(86400)

# ─────────────────────── EVENTS ─────────────────────────────
@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    user_id = str(member.id)

    # ─────────── Connexion au vocal ───────────
    if after.channel and not before.channel:
        voice_times[user_id] = datetime.utcnow()

    # ─────────── Déconnexion du vocal ─────────
    elif before.channel and not after.channel:
        joined_at = voice_times.pop(user_id, None)
        if joined_at:
            seconds_spent = (datetime.utcnow() - joined_at).total_seconds()
            minutes_spent = int(seconds_spent // 60)

            if minutes_spent >= 1:
                gained_xp = minutes_spent * 5  # Exemple : 5 XP par minute
                xp_data = load_xp()

                if user_id not in xp_data:
                    xp_data[user_id] = {"xp": 0, "level": 0}

                xp_data[user_id]["xp"] += gained_xp

                old_level = xp_data[user_id]["level"]
                new_level = get_level(xp_data[user_id]["xp"])

                if new_level > old_level:
                    xp_data[user_id]["level"] = new_level
                    try:
                        channel = member.guild.get_channel(LEVEL_UP_CHANNEL)
                        if channel:
                            xp = xp_data[user_id]["xp"]
                            xp_needed = (new_level + 1) ** 2 * 100

                            image = await generate_rank_card(member, new_level, xp, xp_needed)
                            file = discord.File(fp=image, filename="level_up.png")

                            await channel.send(
                                content=f"🎉 {member.mention} est passé **niveau {new_level}** !",
                                file=file
                            )
                    except Exception as e:
                        logging.error(f"Erreur XP vocal : {e}")

                save_xp(xp_data)

    # ─────────── Suppression vocaux temporaires ───────────
    if before.channel and before.channel.id in TEMP_VC_IDS and not before.channel.members:
        await before.channel.delete(reason="Salon temporaire vide")
        TEMP_VC_IDS.discard(before.channel.id)

# ─────────────────────── DÉMARRAGE DU BOT ────────────────────
async def send_vc_buttons_message():
    await bot.wait_until_ready()
    channel = bot.get_channel(LOBBY_TEXT_CHANNEL)

    if not channel:
        logging.error("❌ Salon LOBBY introuvable (LOBBY_TEXT_CHANNEL).")
        return

    try:
        # Vérifie si un message avec les boutons existe déjà (par son contenu)
        async for message in channel.history(limit=50):
            if message.author == bot.user and "Crée ton salon vocal temporaire" in message.content:
                logging.info("✅ Le message de création de salons existe déjà.")
                return

        await channel.send(
            "🎙️ **Crée ton salon vocal temporaire :**",
            view=VCButtonView()
        )
        logging.info("✅ Message de salons vocaux envoyé dans le lobby.")

    except Exception as e:
        logging.error(f"❌ Impossible d'envoyer le message de salons vocaux : {e}")
        
async def _setup_hook():
    await bot.tree.sync()
    bot.add_view(VCButtonView())
    asyncio.create_task(_reminder_loop())
    asyncio.create_task(auto_backup_xp())
    await send_vc_buttons_message()  # Appelé une seule fois

bot.setup_hook = _setup_hook

if __name__ == "__main__":
    bot.run(TOKEN)
