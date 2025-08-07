import os
import re
import json
import random
import logging
import asyncio
from pathlib import Path
from datetime import datetime

import discord
from discord import app_commands, Embed
from discord.ext import commands
from discord.ui import Button, View
from dotenv import load_dotenv

# ─────────────────────── IMPORTS LOCAUX ─────────────────────
# Doit exister dans ton projet (vue avec boutons PC/Console)
from view import PlayerTypeView

# ─────────────────────── LOGGER ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ─────────────────────── CONFIG ─────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Fichiers de données
XP_FILE = "data/data.json"
BACKUP_FILE = "data/backup.json"

# IDs salons / catégories (mets les tiens)
LEVEL_UP_CHANNEL    = 1402419913716531352
CHANNEL_ROLES       = 1400560866478395512
CHANNEL_WELCOME     = 1400550333796716574
LOBBY_TEXT_CHANNEL  = 1402258805533970472
TEMP_VC_CATEGORY    = 1400559884117999687
TIKTOK_ANNOUNCE_CH  = 1400552164979507263  # utilisé par LiveTikTokView

# Boutons vocaux (noms et emojis)
VC_PROFILES = {
    "PC": {"emoji": "💻"},
    "Crossplay": {"emoji": "🔀"},
    "Consoles": {"emoji": "🎮"},
    "Chat": {"emoji": "💬"},
}

# Pattern pour les noms "PC", "PC 2", etc.
VOC_PATTERN = re.compile(r"^(PC|Crossplay|Consoles|Chat)(?: (\d+))?$", re.I)

# Marque du message permanent (pour le retrouver au redémarrage)
PERMA_MESSAGE_MARK = "[VC_BUTTONS_PERMANENT]"

# ─────────────────────── INTENTS / BOT ──────────────────────
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────── ETATS RUNTIME ──────────────────────
voice_times: dict[str, datetime] = {}   # user_id -> datetime d'entrée
TEMP_VC_IDS: set[int] = set()          # ids des salons vocaux temporaires


# ─────────────────────── HELPERS ────────────────────────────
def ensure_data_dir():
    Path(XP_FILE).parent.mkdir(parents=True, exist_ok=True)

def save_xp(data: dict):
    """Sauvegarde le JSON XP + assure la présence du dossier."""
    ensure_data_dir()
    Path(XP_FILE).write_text(json.dumps(data, indent=4), encoding="utf-8")

def load_xp() -> dict:
    """Charge les XP. Si corrompu, tente une restauration depuis backup."""
    ensure_data_dir()
    path = Path(XP_FILE)
    backup_path = Path(BACKUP_FILE)

    try:
        if not path.exists():
            path.write_text("{}", encoding="utf-8")
            logging.info("📁 Fichier XP manquant, créé automatiquement.")
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("⚠️ Fichier XP corrompu ! Tentative de restauration depuis backup.json...")
        if backup_path.exists():
            try:
                data = json.loads(backup_path.read_text(encoding="utf-8"))
                path.write_text(json.dumps(data, indent=4), encoding="utf-8")
                logging.info("✅ Restauration réussie depuis backup.json.")
                return data
            except Exception as e:
                logging.error(f"❌ Impossible de lire le backup : {e}")
                return {}
        else:
            logging.error("❌ Aucun backup disponible pour restaurer.")
            return {}

def get_level(xp: int) -> int:
    level = 0
    while xp >= (level + 1) ** 2 * 100:
        level += 1
    return level

async def safe_respond(inter: discord.Interaction, content=None, **kwargs):
    """Répond sans planter même si l'interaction a déjà reçu une réponse."""
    try:
        if inter.response.is_done():
            await inter.followup.send(content or "✅", **kwargs)
        else:
            await inter.response.send_message(content or "✅", **kwargs)
    except Exception as e:
        logging.error(f"Réponse interaction échouée: {e}")

async def generate_rank_card(user: discord.User, level: int, xp: int, xp_needed: int):
    from PIL import Image, ImageDraw, ImageFont
    import io

    img = Image.new("RGB", (460, 140), color=(30, 41, 59))  # gris bleuté
    draw = ImageDraw.Draw(img)

    # Titre + infos
    draw.text((16, 14), f"{user.name} — Niveau {level}", fill=(255, 255, 255))
    draw.text((16, 52), f"XP: {xp} / {xp_needed}", fill=(220, 220, 220))

    # Barre de progression (simple)
    bar_x, bar_y, bar_w, bar_h = 16, 90, 428, 22
    draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=(71, 85, 105))
    ratio = max(0.0, min(1.0, xp / max(1, xp_needed)))
    draw.rectangle([bar_x, bar_y, bar_x + int(bar_w * ratio), bar_y + bar_h], fill=(34, 197, 94))

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

def next_vc_name(guild: discord.Guild, base: str) -> str:
    existing_numbers = [
        int(m.group(2))
        for ch in guild.voice_channels
        if (m := VOC_PATTERN.match(ch.name)) and m.group(1).lower() == base.lower()
    ]
    n = (max(existing_numbers) + 1) if existing_numbers else 1
    return base if n == 1 else f"{base} {n}"


# ─────────────────────── TÂCHES DE FOND ────────────────────
async def auto_backup_xp(interval_seconds: int = 3600):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            source = Path(XP_FILE)
            backup = Path(BACKUP_FILE)
            if source.exists():
                backup.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                logging.info("💾 Sauvegarde automatique effectuée.")
        except Exception as e:
            logging.error(f"❌ Erreur lors de la sauvegarde automatique : {e}")
        await asyncio.sleep(interval_seconds)

async def ensure_vc_buttons_message():
    """Poste (ou ré-attache) le panneau permanent des boutons VC dans le salon lobby."""
    await bot.wait_until_ready()
    channel = bot.get_channel(LOBBY_TEXT_CHANNEL)
    if not isinstance(channel, discord.TextChannel):
        logging.warning(f"❌ Salon lobby introuvable: {LOBBY_TEXT_CHANNEL}")
        return

    view = VCButtonView()

    # Cherche un ancien message marqué
    try:
        async for msg in channel.history(limit=100):
            if msg.author == bot.user and PERMA_MESSAGE_MARK in (msg.content or ""):
                try:
                    await msg.edit(view=view)
                    logging.info("🔁 Message permanent des salons vocaux réattaché.")
                    return
                except Exception as e:
                    logging.error(f"Erreur réattachement view: {e}")
                    break
    except Exception as e:
        logging.error(f"Erreur lecture historique: {e}")

    # Sinon poste un nouveau message permanent
    try:
        content = (
            f"{PERMA_MESSAGE_MARK}\n"
            "👋 **Crée ton salon vocal temporaire** :\n"
            "Clique sur un bouton ci-dessous. Le salon sera **supprimé quand il sera vide**."
        )
        await channel.send(content, view=view)
        logging.info("📌 Message permanent des salons vocaux publié.")
    except Exception as e:
        logging.error(f"Erreur envoi message permanent: {e}")

async def reminder_loop_24h():
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
        await asyncio.sleep(86400)  # 24 h


# ─────────────────────── VIEWS ─────────────────────────────
class LiveTikTokView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔴 Annoncer le live TikTok", style=discord.ButtonStyle.danger, custom_id="announce_live")
    async def announce_live(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = bot.get_channel(TIKTOK_ANNOUNCE_CH)
        if channel:
            await channel.send("🚨 Kevin est en LIVE sur TikTok !\n🔴 Rejoins maintenant : https://www.tiktok.com/@kevinlerefuge")
            await safe_respond(interaction, "✅ Le live a été annoncé !", ephemeral=True)
        else:
            await safe_respond(interaction, "❌ Salon cible introuvable.", ephemeral=True)

class VCButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def create_vc(self, interaction: discord.Interaction, profile: str):
        guild = interaction.guild
        if guild is None:
            await safe_respond(interaction, "❌ Action impossible en DM.", ephemeral=True)
            return

        category = guild.get_channel(TEMP_VC_CATEGORY)
        if not isinstance(category, discord.CategoryChannel):
            await safe_respond(interaction, "❌ Catégorie vocale temporaire introuvable.", ephemeral=True)
            return

        name = next_vc_name(guild, profile)
        try:
            vc = await guild.create_voice_channel(
                name=name,
                category=category,
                reason=f"Salon temporaire ({profile}) demandé par {interaction.user}",
            )
            TEMP_VC_IDS.add(vc.id)

            if interaction.user.voice and interaction.user.voice.channel:
                await interaction.user.move_to(vc, reason="Création de salon temporaire")
                moved_text = f"Tu as été déplacé dans **{vc.name}**."
            else:
                moved_text = f"Rejoins **{vc.name}** quand tu veux."

            await safe_respond(
                interaction,
                f"✅ Salon **{vc.name}** créé. {moved_text}\n_Ce salon sera supprimé lorsqu'il sera vide._",
                ephemeral=True
            )
        except Exception as e:
            logging.error(f"Erreur création VC: {e}")
            await safe_respond(interaction, "❌ Impossible de créer le salon.", ephemeral=True)

    @discord.ui.button(label="💻 PC", style=discord.ButtonStyle.primary, custom_id="create_vc_pc")
    async def btn_pc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_vc(interaction, "PC")

    @discord.ui.button(label="🎮 Consoles", style=discord.ButtonStyle.primary, custom_id="create_vc_consoles")
    async def btn_consoles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_vc(interaction, "Consoles")

    @discord.ui.button(label="🔀 Crossplay", style=discord.ButtonStyle.primary, custom_id="create_vc_crossplay")
    async def btn_crossplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_vc(interaction, "Crossplay")

    @discord.ui.button(label="💬 Chat", style=discord.ButtonStyle.secondary, custom_id="create_vc_chat")
    async def btn_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_vc(interaction, "Chat")


# ─────────────────────── COMMANDES SLASH ──────────────────
@bot.tree.command(name="type_joueur", description="Choisir PC ou Console")
@app_commands.checks.has_permissions(manage_guild=True)
async def type_joueur(interaction: discord.Interaction):
    await safe_respond(interaction, f"Les boutons ont été postés dans <#{CHANNEL_ROLES}> 😉", ephemeral=True)
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
    await safe_respond(interaction, "Sondage créé ✔️", ephemeral=True)

@bot.tree.command(name="liendiscord", description="Affiche le lien pour rejoindre le serveur Discord")
async def liendiscord(interaction: discord.Interaction):
    await safe_respond(
        interaction,
        "🔗 Voici le lien pour rejoindre notre serveur :\nhttps://discord.gg/vaJeReXM",
        ephemeral=False
    )

@bot.tree.command(name="rang", description="Affiche ton niveau actuel")
async def rang(interaction: discord.Interaction):
    xp_data = load_xp()
    user_id = str(interaction.user.id)
    if user_id not in xp_data:
        await safe_respond(interaction, "Tu n'as pas encore de niveau... Commence à discuter !", ephemeral=True)
        return
    data = xp_data[user_id]
    await safe_respond(
        interaction,
        f"📊 {interaction.user.mention}, tu es niveau {data['level']} avec {data['xp']} XP.",
        ephemeral=True
    )

@bot.tree.command(name="rang_visuel", description="Affiche ton niveau avec une carte graphique")
async def rang_visuel(interaction: discord.Interaction):
    xp_data = load_xp()
    user_id = str(interaction.user.id)

    if user_id not in xp_data:
        await safe_respond(interaction, "Tu n'as pas encore de niveau... Commence à discuter !", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    data = xp_data[user_id]
    level = data["level"]
    xp = data["xp"]
    xp_next = (level + 1) ** 2 * 100  # formule XP suivante

    image = await generate_rank_card(interaction.user, level, xp, xp_next)
    file = discord.File(fp=image, filename="rank.png")
    await interaction.followup.send(file=file, ephemeral=True)

@bot.tree.command(name="sauvegarder", description="Forcer la sauvegarde manuelle des niveaux (admin uniquement)")
async def sauvegarder(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await safe_respond(interaction, "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
        return

    try:
        source = Path(XP_FILE)
        backup = Path(BACKUP_FILE)
        if source.exists():
            backup.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            await safe_respond(interaction, "💾 Sauvegarde XP manuelle effectuée avec succès !", ephemeral=True)
            logging.info("💾 Sauvegarde manuelle déclenchée par un admin.")
        else:
            await safe_respond(interaction, "⚠️ Aucun fichier de données XP trouvé.", ephemeral=True)
    except Exception as e:
        logging.error(f"❌ Erreur lors de la sauvegarde manuelle : {e}")
        await safe_respond(interaction, "❌ Une erreur est survenue lors de la sauvegarde.", ephemeral=True)

@bot.tree.command(name="vocaux", description="Publier (ou ré-attacher) les boutons pour créer des salons vocaux")
@app_commands.checks.has_permissions(manage_guild=True)
async def vocaux(interaction: discord.Interaction):
    await safe_respond(interaction, "⏳ Je (ré)publie les boutons dans le salon lobby…", ephemeral=True)
    await ensure_vc_buttons_message()
    await interaction.followup.send("📌 Boutons OK dans le salon prévu.", ephemeral=True)

@bot.tree.command(name="purge", description="Supprime N messages récents de ce salon (réservé à Kevin)")
@app_commands.describe(nb="Nombre de messages à supprimer (1-100)")
async def purge(interaction: discord.Interaction, nb: app_commands.Range[int, 1, 100]):
    # Réservé au propriétaire
    if interaction.user.id != OWNER_ID:
        await safe_respond(interaction, "❌ Commande réservée au propriétaire.", ephemeral=True)
        return

    # Doit être un salon texte
    if not isinstance(interaction.channel, discord.TextChannel):
        await safe_respond(interaction, "❌ Utilisable uniquement dans un salon texte.", ephemeral=True)
        return

    # Permissions du bot
    me = interaction.guild.me
    if not interaction.channel.permissions_for(me).manage_messages:
        await safe_respond(interaction, "❌ Je n'ai pas la permission **Gérer les messages** dans ce salon.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        # Tentative rapide (bulk) — ne marche pas si messages > 14 jours
        deleted = await interaction.channel.purge(limit=nb, check=lambda m: not m.pinned, bulk=True)
        await interaction.followup.send(f"🧹 {len(deleted)} messages supprimés.", ephemeral=True)

    except Exception as e:
        logging.warning(f"Purge bulk échouée, fallback lent. Raison: {e}")

        # Fallback lent (supprime 1 par 1) — fonctionne même si > 14 jours, mais plus long
        count = 0
        try:
            async for msg in interaction.channel.history(limit=nb):
                if msg.pinned:
                    continue
                try:
                    await msg.delete()
                    count += 1
                except Exception as ee:
                    logging.error(f"Suppression d'un message échouée: {ee}")
                    continue

            await interaction.followup.send(f"🧹 {count} messages supprimés (mode lent).", ephemeral=True)
        except Exception as ee:
            logging.error(f"Erreur lors de la purge lente: {ee}")
            await interaction.followup.send("❌ Impossible de supprimer les messages.", ephemeral=True)


# ─────────────────────── EVENTS ─────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    # Ignore bots / DMs
    if message.author.bot or not message.guild:
        return

    xp_data = load_xp()
    user_id = str(message.author.id)

    # Init
    if user_id not in xp_data:
        xp_data[user_id] = {"xp": 0, "level": 0}

    # Gain XP aléatoire par message
    gained_xp = random.randint(5, 10)
    xp_data[user_id]["xp"] += gained_xp

    # Level up ?
    old_level = xp_data[user_id]["level"]
    new_level = get_level(xp_data[user_id]["xp"])

    if new_level > old_level:
        xp_data[user_id]["level"] = new_level
        try:
            channel = message.guild.get_channel(LEVEL_UP_CHANNEL)
            if channel:
                xp = xp_data[user_id]["xp"]
                xp_needed = (new_level + 1) ** 2 * 100
                image = await generate_rank_card(message.author, new_level, xp, xp_needed)
                file = discord.File(fp=image, filename="level_up.png")
                await channel.send(content=f"🎉 {message.author.mention} est passé **niveau {new_level}** !", file=file)
        except Exception as e:
            logging.error(f"Erreur lors de l'envoi de la carte de niveau : {e}")

    save_xp(xp_data)
    await bot.process_commands(message)

@bot.event
async def on_member_join(member: discord.Member):
    channel = bot.get_channel(CHANNEL_WELCOME)
    if not isinstance(channel, discord.TextChannel):
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

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    user_id = str(member.id)

    # ───────── Connexion au vocal ─────────
    if after.channel and not before.channel:
        voice_times[user_id] = datetime.utcnow()

    # ───────── Déconnexion du vocal ───────
    elif before.channel and not after.channel:
        joined_at = voice_times.pop(user_id, None)
        if joined_at:
            seconds_spent = (datetime.utcnow() - joined_at).total_seconds()
            minutes_spent = int(seconds_spent // 60)

            if minutes_spent >= 1:
                gained_xp = minutes_spent * 5
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
                            await channel.send(content=f"🎉 {member.mention} est passé **niveau {new_level}** !", file=file)
                    except Exception as e:
                        logging.error(f"Erreur XP vocal : {e}")

                save_xp(xp_data)

    # ───────── Changement de salon ────────
    elif before.channel and after.channel and before.channel != after.channel:
        joined_at = voice_times.get(user_id)
        if joined_at:
            seconds_spent = (datetime.utcnow() - joined_at).total_seconds()
            minutes_spent = int(seconds_spent // 60)
            if minutes_spent >= 1:
                gained_xp = minutes_spent * 5
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
                            await channel.send(content=f"🎉 {member.mention} est passé **niveau {new_level}** !", file=file)
                    except Exception as e:
                        logging.error(f"Erreur XP vocal (move): {e}")
                save_xp(xp_data)

        # redémarre le chrono dans le nouveau salon
        voice_times[user_id] = datetime.utcnow()

    # ───────── Suppression des salons temporaires vides ─────
    if before.channel and before.channel.id in TEMP_VC_IDS and not before.channel.members:
        try:
            await before.channel.delete(reason="Salon temporaire vide")
            TEMP_VC_IDS.discard(before.channel.id)
        except Exception as e:
            logging.error(f"Suppression VC temporaire échouée: {e}")


# ─────────────────────── SETUP HOOK ────────────────────────
async def _setup_hook():
    # Vues persistantes (survivent aux redémarrages)
    bot.add_view(VCButtonView())
    bot.add_view(LiveTikTokView())

    await bot.tree.sync()

    # Tâches de fond
    asyncio.create_task(reminder_loop_24h())
    asyncio.create_task(auto_backup_xp())
    asyncio.create_task(ensure_vc_buttons_message())

bot.setup_hook = _setup_hook


# ─────────────────────── MAIN ──────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN manquant. Vérifie ton .env")
    bot.run(TOKEN)
