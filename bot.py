import os
import re
import json
import random
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands, Embed
from discord.ext import commands
from discord.ui import Button, View
from dotenv import load_dotenv

# ── XP CONFIG ───────────────────────────────────────────────
MSG_XP = 8               # XP par message texte
VOICE_XP_PER_MIN = 3     # XP par minute en vocal

# ─────────────────────── ENV & LOGGING ─────────────────────
load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# accepte plusieurs clés possibles et logge si rien
TOKEN = (
    os.getenv("DISCORD_TOKEN")
    or os.getenv("TOKEN")
    or os.getenv("BOT_TOKEN")
)

if not TOKEN:
    # aide au debug : liste les clés liées au token visibles à runtime
    seen = [k for k in os.environ.keys() if "TOKEN" in k or "DISCORD" in k]
    logging.error("Aucun token trouvé. Clés visibles: %s", ", ".join(sorted(seen)) or "aucune")
    raise RuntimeError("DISCORD_TOKEN manquant. Ajoute la variable dans Railway > Service > Variables")

# ─────────────────────── IMPORTS LOCAUX ─────────────────────
from view import PlayerTypeView

# ─────────────────────── CONFIG ─────────────────────────────
# Fichiers de données
XP_FILE = "data/data.json"
BACKUP_FILE = "data/backup.json"
DAILY_STATS_FILE = "data/daily_stats.json"  # stats quotidiennes (msg + minutes vocal)

# IDs salons / catégories (à adapter)
LEVEL_UP_CHANNEL    = 1402419913716531352
CHANNEL_ROLES       = 1400560866478395512
CHANNEL_WELCOME     = 1400550333796716574
LOBBY_TEXT_CHANNEL  = 1402258805533970472
TEMP_VC_CATEGORY    = 1400559884117999687
TIKTOK_ANNOUNCE_CH  = 1400552164979507263  # bouton live
ACTIVITY_SUMMARY_CH = 1400552164979507263  # résumé quotidien (même salon que TikTok)

# Catégories pour /lfg (vocal créé dans cette catégorie)
LFG_CATEGORIES = {
    "fps":        1400553078373089301,
    "mmo-rpg":    1400553114918064178,
    "battleroyal":1400553162594582641,
    "strategie":  1400554881663631513,
    "consoles":   1400553622919712868,
}

PARIS_TZ = ZoneInfo("Europe/Paris")
OWNER_ID: int = int(os.getenv("OWNER_ID", "541417878314942495"))

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
voice_times: dict[str, datetime] = {}   # user_id -> datetime d'entrée (UTC)
TEMP_VC_IDS: set[int] = set()          # ids des salons vocaux temporaires
LFG_SESSIONS: dict[int, dict] = {}     # message_id -> session LFG

# ─────────────────────── HELPERS ────────────────────────────

def ensure_data_dir():
    Path(XP_FILE).parent.mkdir(parents=True, exist_ok=True)

def save_json(path: str, data: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def save_xp(data: dict):
    ensure_data_dir()
    save_json(XP_FILE, data)

def load_xp() -> dict:
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
                path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
                logging.info("✅ Restauration réussie depuis backup.json.")
                return data
            except Exception as e:
                logging.error(f"❌ Impossible de lire le backup : {e}")
                return {}
        else:
            logging.error("❌ Aucun backup disponible pour restaurer.")
            return {}

def load_daily_stats() -> dict:
    return load_json(DAILY_STATS_FILE)

def save_daily_stats(d: dict):
    save_json(DAILY_STATS_FILE, d)

def get_level(xp: int) -> int:
    level = 0
    while xp >= (level + 1) ** 2 * 100:
        level += 1
    return level

async def safe_respond(inter: discord.Interaction, content=None, **kwargs):
    try:
        if inter.response.is_done():
            await inter.followup.send(content or "✅", **kwargs)
        else:
            await inter.response.send_message(content or "✅", **kwargs)
    except Exception as e:
        logging.error(f"Réponse interaction échouée: {e}")

async def generate_rank_card(user: discord.User, level: int, xp: int, xp_needed: int):
    from PIL import Image, ImageDraw
    import io
    img = Image.new("RGB", (460, 140), color=(30, 41, 59))
    draw = ImageDraw.Draw(img)
    draw.text((16, 14), f"{user.name} — Niveau {level}", fill=(255, 255, 255))
    draw.text((16, 52), f"XP: {xp} / {xp_needed}", fill=(220, 220, 220))
    bar_x, bar_y, bar_w, bar_h = 16, 90, 428, 22
    draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=(71, 85, 105))
    ratio = max(0.0, min(1.0, xp / max(1, xp_needed)))
    draw.rectangle([bar_x, bar_y, bar_x + int(bar_w * ratio), bar_y + bar_h], fill=(34, 197, 94))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

async def announce_level_up(guild: discord.Guild, member: discord.Member, old_level: int, new_level: int, xp: int):
    """Annonce un level-up dans le salon niveaux avec avatar + carte."""
    channel = guild.get_channel(LEVEL_UP_CHANNEL)
    if not isinstance(channel, discord.TextChannel):
        logging.warning("Salon niveaux introuvable ou invalide.")
        return

    try:
        xp_needed = (new_level + 1) ** 2 * 100
        image = await generate_rank_card(member, new_level, xp, xp_needed)
        file = discord.File(fp=image, filename="level_up.png")

        embed = discord.Embed(
            title=f"🚀 Niveau {new_level} débloqué !",
            description=(
                f"{member.mention} **passe de {old_level} ➜ {new_level}**\n"
                f"XP : **{xp} / {xp_needed}**"
            ),
            color=0xF4B400,
            timestamp=datetime.now(PARIS_TZ)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_image(url="attachment://level_up.png")
        embed.set_footer(text="GG !")

        await channel.send(content=f"🎉 {member.mention}", embed=embed, file=file)
    except Exception as e:
        logging.error(f"Annonce level-up échouée: {e}")


def next_vc_name(guild: discord.Guild, base: str) -> str:
    existing_numbers = [
        int(m.group(2))
        for ch in guild.voice_channels
        if (m := VOC_PATTERN.match(ch.name)) and m.group(1).lower() == base.lower()
    ]
    n = (max(existing_numbers) + 1) if existing_numbers else 1
    return base if n == 1 else f"{base} {n}"

def parse_when(when_str: str) -> datetime:
    s = when_str.strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=PARIS_TZ)
        return dt
    except ValueError:
        pass
    try:
        h, m = map(int, s.split(":"))
        now = datetime.now(PARIS_TZ)
        dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt
    except Exception:
        raise ValueError("Format d'heure invalide. Utilise 'HH:MM' ou 'YYYY-MM-DD HH:MM'.")

def incr_daily_stat(guild_id: int, user_id: int, *, msg_inc: int = 0, voice_min_inc: int = 0):
    """Incrémente les stats du jour (Europe/Paris)."""
    stats = load_daily_stats()
    g = str(guild_id)
    date_key = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
    stats.setdefault(g, {}).setdefault(date_key, {}).setdefault(str(user_id), {"msg": 0, "voice_min": 0})
    stats[g][date_key][str(user_id)]["msg"] += msg_inc
    stats[g][date_key][str(user_id)]["voice_min"] += voice_min_inc
    save_daily_stats(stats)

# ─────────────────────── /LFG VIEW ─────────────────────────
class LFGJoinView(View):
    def __init__(self, session_msg_id: int):
        super().__init__(timeout=60*60*12)
        self.session_msg_id = session_msg_id

    @discord.ui.button(label="✅ Je viens", style=discord.ButtonStyle.success, custom_id="lfg_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = LFG_SESSIONS.get(self.session_msg_id)
        if not sess:
            await safe_respond(interaction, "❌ Cette session n'existe plus.", ephemeral=True)
            return
        members: set[int] = sess["members"]
        if interaction.user.id in members:
            await safe_respond(interaction, "🔁 Tu es déjà inscrit.", ephemeral=True)
            return
        members.add(interaction.user.id)
        await safe_respond(interaction, "✅ Inscription enregistrée !", ephemeral=True)
        try:
            msg = await interaction.channel.fetch_message(self.session_msg_id)
            emb = msg.embeds[0] if msg.embeds else discord.Embed(title="Session LFG")
            emb.set_footer(text=f"Participants: {len(members)}")
            await msg.edit(embed=emb, view=self)
        except Exception as e:
            logging.error(f"Maj embed LFG échouée: {e}")

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

# /rang → image (et /rang_visuel supprimé)
@bot.tree.command(name="rang", description="Affiche ton niveau avec une carte graphique")
async def rang(interaction: discord.Interaction):
    # 1) Chargement XP
    xp_data = load_xp()
    user_id = str(interaction.user.id)
    if user_id not in xp_data:
        await interaction.response.send_message(
            "Tu n'as pas encore de niveau... Commence à discuter !",
            ephemeral=True
        )
        return

    # 2) Défer pour éviter le timeout (et montrer le spinner)
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass  # si déjà répondu quelque part, on ignore

    try:
        data = xp_data[user_id]
        level = data.get("level", 0)
        xp = data.get("xp", 0)
        xp_next = (level + 1) ** 2 * 100

        # 3) Génération de l'image
        image = await generate_rank_card(interaction.user, level, xp, xp_next)
        file = discord.File(fp=image, filename="rank.png")

        # 4) Tentative 1 : envoi EPHEMERAL avec fichier
        try:
            await interaction.followup.send(file=file, ephemeral=True)
            return
        except discord.Forbidden:
            pass
        except discord.HTTPException as e:
            logging.warning(f"/rang: envoi ephemeral échoué, fallback public. Raison: {e}")

        # 5) Fallback : envoi public
        try:
            await interaction.channel.send(
                content=f"{interaction.user.mention} voici ta carte de niveau :",
                file=file
            )
            await interaction.followup.send(
                "Je n'ai pas pu l'envoyer en privé, je l'ai postée dans le salon.",
                ephemeral=True
            )
        except Exception as e:
            logging.exception(f"/rang: échec envoi public: {e}")
            await interaction.followup.send(
                "❌ Impossible d'envoyer l'image (vérifie la permission **Joindre des fichiers** pour le bot).",
                ephemeral=True
            )

    except ImportError as e:
        logging.exception(f"/rang: ImportError (Pillow manquante ?) {e}")
        await interaction.followup.send(
            "❌ Erreur interne: dépendance manquante (Pillow).",
            ephemeral=True
        )
    except Exception as e:
        logging.exception(f"/rang: exception inattendue: {e}")
        await interaction.followup.send(
            "❌ Une erreur est survenue pendant la génération de la carte.",
            ephemeral=True
        )

@bot.tree.command(name="vocaux", description="Publier (ou ré-attacher) les boutons pour créer des salons vocaux")
@app_commands.checks.has_permissions(manage_guild=True)
async def vocaux(interaction: discord.Interaction):
    await safe_respond(interaction, "⏳ Je (ré)publie les boutons dans le salon lobby…", ephemeral=True)
    await ensure_vc_buttons_message()
    await interaction.followup.send("📌 Boutons OK dans le salon prévu.", ephemeral=True)

@bot.tree.command(name="purge", description="Supprime N messages récents de ce salon (réservé à Kevin)")
@app_commands.describe(nb="Nombre de messages à supprimer (1-100)")
async def purge(interaction: discord.Interaction, nb: app_commands.Range[int, 1, 100]):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
    except Exception:
        pass
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Commande réservée au propriétaire.", ephemeral=True); return
    if interaction.guild is None:
        await interaction.followup.send("❌ Utilisable uniquement sur un serveur.", ephemeral=True); return
    ch = interaction.channel
    if ch is None:
        await interaction.followup.send("❌ Salon introuvable.", ephemeral=True); return
    me = interaction.guild.me
    perms = ch.permissions_for(me)
    if not perms.manage_messages or not perms.read_message_history:
        await interaction.followup.send("❌ Il me manque les permissions **Gérer les messages** et/ou **Lire l’historique**.", ephemeral=True); return
    try:
        if isinstance(ch, discord.TextChannel):
            deleted = await ch.purge(limit=nb, check=lambda m: not m.pinned, bulk=True)
            await interaction.followup.send(f"🧹 {len(deleted)} messages supprimés.", ephemeral=True); return
    except Exception as e:
        logging.warning(f"Purge bulk échouée, fallback lent. Raison: {e}")
    count = 0
    try:
        async for msg in ch.history(limit=nb):
            if msg.pinned: continue
            try:
                await msg.delete(); count += 1
            except Exception as ee:
                logging.error(f"Suppression d'un message échouée: {ee}")
        await interaction.followup.send(f"🧹 {count} messages supprimés (mode lent).", ephemeral=True)
    except Exception as ee:
        logging.error(f"Erreur lors de la purge lente: {ee}")
        await interaction.followup.send("❌ Impossible de supprimer les messages.", ephemeral=True)

# ─────────────────────── /LFG ──────────────────────────────
@bot.tree.command(name="lfg", description="Créer une session pour chercher des joueurs")
@app_commands.describe(
    jeu="Nom du jeu (ex: Ready or Not)",
    plateforme="Plateforme",
    heure="Heure de début (HH:MM ou YYYY-MM-DD HH:MM, Europe/Paris)",
    categorie="Catégorie où créer le vocal"
)
@app_commands.choices(
    plateforme=[
        app_commands.Choice(name="PC", value="PC"),
        app_commands.Choice(name="Crossplay", value="Crossplay"),
        app_commands.Choice(name="Consoles", value="Consoles"),
    ],
    categorie=[
        app_commands.Choice(name="fps", value="fps"),
        app_commands.Choice(name="mmo-rpg", value="mmo-rpg"),
        app_commands.Choice(name="battleroyal", value="battleroyal"),
        app_commands.Choice(name="strategie", value="strategie"),
        app_commands.Choice(name="consoles", value="consoles"),
    ],
)
async def lfg(interaction: discord.Interaction, jeu: str, plateforme: app_commands.Choice[str], heure: str, categorie: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    try:
        start_dt = parse_when(heure)
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True); return
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("❌ Utilisable uniquement sur un serveur.", ephemeral=True); return
    cat_id = LFG_CATEGORIES.get(categorie.value)
    category = guild.get_channel(cat_id)
    if not isinstance(category, discord.CategoryChannel):
        category = guild.get_channel(TEMP_VC_CATEGORY)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("❌ Catégorie cible introuvable.", ephemeral=True); return
    vc_name = f"{plateforme.value} {jeu}"
    try:
        voice = await guild.create_voice_channel(name=vc_name, category=category, reason=f"LFG par {interaction.user} | {jeu}")
        TEMP_VC_IDS.add(voice.id)
    except Exception as e:
        logging.error(f"Création VC LFG échouée: {e}")
        await interaction.followup.send("❌ Impossible de créer le salon vocal.", ephemeral=True); return
    dt_str = start_dt.strftime("%Y-%m-%d %H:%M")
    emb = discord.Embed(
        title="🎮 Session LFG",
        description=(f"**Jeu :** {jeu}\n**Plateforme :** {plateforme.value}\n**Heure :** {dt_str} (Europe/Paris)\n**Vocal :** <#{voice.id}>\n"),
        color=0x00C896
    )
    emb.set_footer(text="Participants: 1")
    try:
        msg = await interaction.channel.send(embed=emb)
        thread = await msg.create_thread(name=f"LFG • {jeu} • {dt_str}")
    except Exception as e:
        logging.error(f"Création message/thread LFG échouée: {e}")
        await interaction.followup.send("❌ Impossible de créer le thread.", ephemeral=True); return
    session_key = msg.id
    LFG_SESSIONS[session_key] = {
        "creator": interaction.user.id,
        "members": {interaction.user.id},
        "thread_id": thread.id,
        "vc_id": voice.id,
        "when": start_dt,
        "jeu": jeu,
        "plateforme": plateforme.value,
    }
    try:
        await msg.edit(view=LFGJoinView(session_msg_id=session_key))
    except Exception as e:
        logging.error(f"Attache view LFG échouée: {e}")
    await interaction.followup.send(f"✅ Session créée ! Thread : <#{thread.id}> | Vocal : <#{voice.id}>", ephemeral=True)

    async def reminder_task(key: int):
        sess = LFG_SESSIONS.get(key)
        if not sess: return
        when: datetime = sess["when"]
        remind_at = when - timedelta(minutes=10)
        now = datetime.now(PARIS_TZ)
        delay = (remind_at - now).total_seconds()
        if delay > 0: await asyncio.sleep(delay)
        thread_ch = bot.get_channel(sess["thread_id"])
        if not isinstance(thread_ch, (discord.Thread, discord.TextChannel)): return
        members = sess["members"]; mentions = " ".join(f"<@{uid}>" for uid in members) if members else ""
        try:
            await thread_ch.send(content=f"{mentions}\n⏰ **Rappel** : session dans 10 minutes ({when.strftime('%H:%M')}). Rejoignez le vocal ➜ <#{sess['vc_id']}>")
        except Exception as e:
            logging.error(f"Envoi rappel LFG échoué: {e}")

    async def close_task(key: int):
        sess = LFG_SESSIONS.get(key)
        if not sess: return
        when: datetime = sess["when"]
        close_at = when + timedelta(hours=1)
        now = datetime.now(PARIS_TZ)
        delay = (close_at - now).total_seconds()
        if delay > 0: await asyncio.sleep(delay)
        thread_ch = bot.get_channel(sess["thread_id"])
        if isinstance(thread_ch, discord.Thread):
            try:
                await thread_ch.send("⏱️ Session terminée — le thread est archivé. GG à tous !")
                await thread_ch.edit(archived=True, locked=True)
            except Exception as e:
                logging.error(f"Archivage thread LFG échoué: {e}")
        vc = bot.get_channel(sess["vc_id"])
        if isinstance(vc, discord.VoiceChannel):
            try:
                if not vc.members:
                    await vc.delete(reason="LFG terminé (salon vide)")
                    TEMP_VC_IDS.discard(vc.id)
            except Exception as e:
                logging.error(f"Suppression VC LFG échouée: {e}")
        LFG_SESSIONS.pop(key, None)

    asyncio.create_task(reminder_task(session_key))
    asyncio.create_task(close_task(session_key))

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
    """
    Mini‑patch: si le réattachement échoue, on republie un NOUVEAU message avec la vue fraîche.
    """
    await bot.wait_until_ready()
    channel = bot.get_channel(LOBBY_TEXT_CHANNEL)
    if not isinstance(channel, discord.TextChannel):
        logging.warning(f"❌ Salon lobby introuvable: {LOBBY_TEXT_CHANNEL}")
        return

    view = VCButtonView()
    found = None

    # On cherche un ancien message "permanent"
    try:
        async for msg in channel.history(limit=100):
            if msg.author == bot.user and PERMA_MESSAGE_MARK in (msg.content or ""):
                found = msg
                break
    except Exception as e:
        logging.error(f"Erreur lecture historique: {e}")

    content = (
        f"{PERMA_MESSAGE_MARK}\n"
        "👋 **Crée ton salon vocal temporaire** :\n"
        "Clique sur un bouton ci-dessous. Le salon sera **supprimé quand il sera vide**."
    )

    if found:
        try:
            await found.edit(content=content, view=view)
            logging.info("🔁 Message permanent réattaché (avec vue).")
            return
        except Exception as e:
            logging.error(f"Échec réattachement, je reposte un nouveau message: {e}")

    try:
        await channel.send(content, view=view)
        logging.info("📌 Message permanent des salons vocaux publié (nouveau).")
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
            if member.bot: continue
            if len(member.roles) <= 1:
                try:
                    await channel.send(f"{member.mention} tu n’as pas encore choisi ton rôle ici. Clique sur un bouton pour sélectionner ta plateforme 🎮💻")
                except Exception as e:
                    logging.error(f"Erreur rappel rôles {member.display_name}: {e}")
        await asyncio.sleep(86400)

async def daily_summary_loop():
    """Chaque minuit (Europe/Paris), poste le résumé d’activité du **jour qui vient de se terminer**."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.now(PARIS_TZ)
        # prochain minuit
        tomorrow = now + timedelta(days=1)
        next_midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        delay = (next_midnight - now).total_seconds()
        await asyncio.sleep(max(1, delay))

        # Jour écoulé (celui qu'on résume)
        day_key = now.strftime("%Y-%m-%d")
        stats = load_daily_stats()

        for guild in bot.guilds:
            gkey = str(guild.id)
            gstats = stats.get(gkey, {}).get(day_key, {})
            if not gstats:
                continue  # rien à résumer pour ce serveur

            # Construit classements
            def u_name(uid: str) -> str:
                member = guild.get_member(int(uid))
                return member.display_name if member else f"User {uid}"

            items = []
            for uid, data in gstats.items():
                msgs = int(data.get("msg", 0))
                vmin = int(data.get("voice_min", 0))
                score = msgs + vmin
                items.append((uid, msgs, vmin, score))

            # Tri
            top_msg  = sorted(items, key=lambda x: x[1], reverse=True)[:5]
            top_vc   = sorted(items, key=lambda x: x[2], reverse=True)[:5]
            top_mvp  = sorted(items, key=lambda x: x[3], reverse=True)[:5]

            total_msgs = sum(x[1] for x in items)
            total_vmin = sum(x[2] for x in items)

            # Compose l'embed
            embed = discord.Embed(
                title=f"📈 Résumé du jour — {day_key}",
                description=f"**Total** : {total_msgs} messages • {total_vmin} min en vocal",
                color=0x00C896
            )
            if top_msg:
                embed.add_field(
                    name="💬 Top Messages",
                    value="\n".join([f"**{i+1}.** {u_name(uid)} — {msgs} msgs"
                                         for i, (uid, msgs, _, _) in enumerate(top_msg)]),
                    inline=False
                )
            if top_vc:
                embed.add_field(
                    name="🎙️ Top Vocal (min)",
                    value="\n".join([f"**{i+1}.** {u_name(uid)} — {vmin} min"
                                         for i, (uid, _, vmin, _) in enumerate(top_vc)]),
                    inline=False
                )
            if top_mvp:
                embed.add_field(
                    name="🏆 MVP (messages + minutes)",
                    value="\n".join([f"**{i+1}.** {u_name(uid)} — {score} pts"
                                         for i, (uid, msgs, vmin, score) in enumerate(top_mvp)]),
                    inline=False
                )

            # Envoi @everyone
            ch = guild.get_channel(ACTIVITY_SUMMARY_CH)
            if isinstance(ch, discord.TextChannel):
                try:
                    me = guild.me
                    if not ch.permissions_for(me).mention_everyone:
                        await ch.send("⚠️ Je n'ai pas la permission de mentionner @everyone ici.")
                    content = "@everyone — Voici les joueurs les plus actifs d'hier !"
                    await ch.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                except Exception as e:
                    logging.error(f"Envoi résumé quotidien échoué (guild {guild.id}): {e}")

        # (Optionnel) purge stats anciennes (> 14 jours)
        try:
            cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
            for gkey in list(stats.keys()):
                for dkey in list(stats.get(gkey, {}).keys()):
                    if dkey < cutoff:
                        stats[gkey].pop(dkey, None)
                if not stats[gkey]:
                    stats.pop(gkey, None)
            save_daily_stats(stats)
        except Exception as e:
            logging.error(f"Purge stats anciennes échouée: {e}")

# ─────────────────────── VIEWS ─────────────────────────────
class LiveTikTokView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔴 Annoncer le live TikTok", style=discord.ButtonStyle.danger, custom_id="announce_live")
    async def announce_live(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = bot.get_channel(TIKTOK_ANNOUNCE_CH)
        if not channel:
            await safe_respond(interaction, "❌ Salon cible introuvable.", ephemeral=True)
            return
        me = interaction.guild.me
        if not channel.permissions_for(me).mention_everyone:
            await safe_respond(interaction, "❌ Je n'ai pas la permission de mentionner @everyone dans ce salon.", ephemeral=True)
            return
        await channel.send(
            "@everyone 🚨 Kevin est en LIVE sur TikTok !\n🔴 Rejoins maintenant : https://www.tiktok.com/@kevinlerefuge",
            allowed_mentions=discord.AllowedMentions(everyone=True)
        )
        await safe_respond(interaction, "✅ Annonce envoyée !", ephemeral=True)

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
                name=name, category=category,
                reason=f"Salon temporaire ({profile}) demandé par {interaction.user}",
            )
            TEMP_VC_IDS.add(vc.id)
            if interaction.user.voice and interaction.user.voice.channel:
                await interaction.user.move_to(vc, reason="Création de salon temporaire")
                moved_text = f"Tu as été déplacé dans **{vc.name}**."
            else:
                moved_text = f"Rejoins **{vc.name}** quand tu veux."
            await safe_respond(interaction, f"✅ Salon **{vc.name}** créé. {moved_text}\n_Ce salon sera supprimé lorsqu'il sera vide._", ephemeral=True)
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

# ─────────────────────── EVENTS ─────────────────────────────
@bot.event
async def on_ready():
    logging.info(f"✅ Connecté en tant que {bot.user} (latence {bot.latency*1000:.0f} ms)")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # Ignorer les commandes (! ou /)
    if message.content.startswith(("!", "/")):
        return await bot.process_commands(message)

    # XP messages (8 XP fixes via MSG_XP)
    xp_data = load_xp()
    user_id = str(message.author.id)
    if user_id not in xp_data:
        xp_data[user_id] = {"xp": 0, "level": 0}

    gained_xp = MSG_XP  # défini plus haut: MSG_XP = 8
    xp_data[user_id]["xp"] += gained_xp

    old_level = xp_data[user_id]["level"]
    new_level = get_level(xp_data[user_id]["xp"])
    if new_level > old_level:
        xp_data[user_id]["level"] = new_level
        try:
            xp = xp_data[user_id]["xp"]
            await announce_level_up(message.guild, message.author, old_level, new_level, xp)
        except Exception as e:
            logging.error(f"Erreur envoi carte niveau : {e}")

    save_xp(xp_data)

    # Stats quotidiennes (messages)
    incr_daily_stat(message.guild.id, message.author.id, msg_inc=1)

    await bot.process_commands(message)

@bot.event
async def on_member_join(member: discord.Member):
    channel = bot.get_channel(CHANNEL_WELCOME)
    if not isinstance(channel, discord.TextChannel):
        logging.warning("❌ Salon de bienvenue introuvable.")
        return
    embed = discord.Embed(
        title="🎉 Bienvenue au Refuge !",
        description=(f"{member.mention}, installe-toi bien !\n🕹️ Choisis ton rôle dans <#{CHANNEL_ROLES}> pour accéder à toutes les sections.\nRavi de t’avoir parmi nous 🎮"),
        color=0x00ffcc
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Membre #{len(member.guild.members)}")
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logging.error(f"Erreur envoi bienvenue : {e}")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    user_id = str(member.id)

    # Connexion au vocal
    if after.channel and not before.channel:
        voice_times[user_id] = datetime.utcnow()

    # Déconnexion du vocal
    elif before.channel and not after.channel:
        joined_at = voice_times.pop(user_id, None)
        if joined_at:
            seconds_spent = (datetime.utcnow() - joined_at).total_seconds()
            minutes_spent = int(seconds_spent // 60)
            if minutes_spent >= 1:
                # XP vocal : 3 XP/min
                gained_xp = minutes_spent * VOICE_XP_PER_MIN
                xp_data = load_xp()
                if user_id not in xp_data:
                    xp_data[user_id] = {"xp": 0, "level": 0}
                xp_data[user_id]["xp"] += gained_xp

                old_level = xp_data[user_id]["level"]
                new_level = get_level(xp_data[user_id]["xp"])
                if new_level > old_level:
                    xp_data[user_id]["level"] = new_level
                    try:
                        xp = xp_data[user_id]["xp"]
                        await announce_level_up(member.guild, member, old_level, new_level, xp)
                    except Exception as e:
                        logging.error(f"Erreur annonce niveau vocal : {e}")

                save_xp(xp_data)
                # Stats quotidiennes (vocal minutes)
                incr_daily_stat(member.guild.id, member.id, voice_min_inc=minutes_spent)

    # Changement de salon
    elif before.channel and after.channel and before.channel != after.channel:
        joined_at = voice_times.get(user_id)
        if joined_at:
            seconds_spent = (datetime.utcnow() - joined_at).total_seconds()
            minutes_spent = int(seconds_spent // 60)
            if minutes_spent >= 1:
                # XP vocal : 3 XP/min (temps dans l'ancien salon)
                gained_xp = minutes_spent * VOICE_XP_PER_MIN
                xp_data = load_xp()
                if user_id not in xp_data:
                    xp_data[user_id] = {"xp": 0, "level": 0}
                xp_data[user_id]["xp"] += gained_xp

                old_level = xp_data[user_id]["level"]
                new_level = get_level(xp_data[user_id]["xp"])
                if new_level > old_level:
                    xp_data[user_id]["level"] = new_level
                    try:
                        xp = xp_data[user_id]["xp"]
                        await announce_level_up(member.guild, member, old_level, new_level, xp)
                    except Exception as e:
                        logging.error(f"Erreur annonce niveau vocal (move): {e}")

                save_xp(xp_data)
                incr_daily_stat(member.guild.id, member.id, voice_min_inc=minutes_spent)

        # redémarre le chrono de présence dans le nouveau salon
        voice_times[user_id] = datetime.utcnow()

    # Suppression des salons temporaires vides
    if before.channel and before.channel.id in TEMP_VC_IDS and not before.channel.members:
        try:
            await before.channel.delete(reason="Salon temporaire vide")
            TEMP_VC_IDS.discard(before.channel.id)
        except Exception as e:
            logging.error(f"Suppression VC temporaire échouée: {e}")


# ─────────────────────── SETUP ─────────────────────────────
async def _setup_hook():
    bot.add_view(VCButtonView())
    bot.add_view(LiveTikTokView())
    await bot.tree.sync()
    asyncio.create_task(reminder_loop_24h())
    asyncio.create_task(auto_backup_xp())
    asyncio.create_task(ensure_vc_buttons_message())
    asyncio.create_task(daily_summary_loop())  # ⬅️ Résumé quotidien

bot.setup_hook = _setup_hook

# ─────────────────────── MAIN ──────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
