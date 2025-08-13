import os
import re
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from discord.ui import View

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ─────────────────────── ENV & LOGGING ─────────────────────
load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

# ─────────────────────── INTENTS / BOT ─────────────────────
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents)
# ── XP CONFIG ───────────────────────────────────────────────
MSG_XP = 8               # XP par message texte
VOICE_XP_PER_MIN = 3     # XP par minute en vocal
# Formule de niveau: seuil (niveau n -> n+1) = (n+1)^2 * 100 XP
REMOVE_LOWER_TIER_ROLES = True

# ── Récompenses par niveau (requis par grant_level_roles)
LEVEL_ROLE_REWARDS = {
    5:  1403510226354700430,  # Bronze
    10: 1403510368340410550,  # Argent
    20: 1403510466818605118,  # Or
}

# ── Rôles plateformes + notifications
ROLE_PC       = 1400560541529018408
ROLE_CONSOLE  = 1400560660710162492
ROLE_MOBILE   = 1404791652085928008
ROLE_NOTIFICATION = 1404882154370109450

# (facultatif, pratique si tu veux itérer)
PLATFORM_ROLE_IDS = {
    "PC": ROLE_PC,
    "Consoles": ROLE_CONSOLE,
    "Mobile": ROLE_MOBILE,
}
TEMP_VC_CATEGORY    = 1400559884117999687  # ID catégorie vocale temporaire

# ── LIMITES & AUTO-RENAME SALONS TEMP ──────────────────────
# Limite par catégorie (par défaut: pas de limite si non présent dans ce dict)
TEMP_VC_LIMITS: dict[int, int] = {
    TEMP_VC_CATEGORY: 5,  # ex: max 5 salons temporaires dans la catégorie "temp"
    # 1400553078373089301: 3,  # (optionnel) limite pour la catégorie LFG "fps"
}

# Auto-rename du salon selon le jeu détecté (Discord Rich Presence)
AUTO_RENAME_ENABLED = True
# Format du nom : {base} = PC/Crossplay/Consoles/Chat | {game} = nom du jeu détecté
AUTO_RENAME_FORMAT = "{base} • {game}"
# Fréquence min entre deux renames pour un même salon (anti-spam)
AUTO_RENAME_COOLDOWN_SEC = 45

# ─────────────────────── CONFIG ─────────────────────────────
LEVEL_UP_CHANNEL    = 1402419913716531352
CHANNEL_ROLES       = 1400560866478395512
CHANNEL_WELCOME     = 1400550333796716574
LOBBY_TEXT_CHANNEL  = 1402258805533970472
TIKTOK_ANNOUNCE_CH  = 1400552164979507263
ACTIVITY_SUMMARY_CH = 1400552164979507263

LFG_CATEGORIES = {
    "fps":        1400553078373089301,
    "mmo-rpg":    1400553114918064178,
    "battleroyal":1400553162594582641,
    "strategie":  1400554881663631513,
    "consoles":   1400553622919712868,
}

PARIS_TZ = ZoneInfo("Europe/Paris")
OWNER_ID: int = int(os.getenv("OWNER_ID", "541417878314942495"))

VC_PROFILES = {
    "PC": {"emoji": "💻"},
    "Crossplay": {"emoji": "🔀"},
    "Consoles": {"emoji": "🎮"},
    "Chat": {"emoji": "💬"},
}

VOC_PATTERN = re.compile(r"^(PC|Crossplay|Consoles|Chat)(?: (\d+))?$", re.I)
PERMA_MESSAGE_MARK = "[VC_BUTTONS_PERMANENT]"
ROLES_PERMA_MESSAGE_MARK = "[ROLES_BUTTONS_PERMANENT]"

# ─────────────────────── ETATS RUNTIME ──────────────────────
voice_times: dict[str, datetime] = {}   # user_id -> datetime d'entrée (naïf UTC)
TEMP_VC_IDS: set[int] = set()          # ids des salons vocaux temporaires
LFG_SESSIONS: dict[int, dict] = {}     # message_id -> session LFG

# Anti-spam: derniers rappels rôles par (guild_id, user_id) -> date 'YYYY-MM-DD'
REMINDER_LAST: dict[tuple[int, int], str] = {}
REMINDER_DAILY_CAP_PER_GUILD = 20  # maximum de rappels/jour/serveur

# ─────────────────────── TOKEN ──────────────────────────────
TOKEN = (
    os.getenv("DISCORD_TOKEN")
    or os.getenv("TOKEN")
    or os.getenv("BOT_TOKEN")
)
if not TOKEN:
    seen = [k for k in os.environ.keys() if "TOKEN" in k or "DISCORD" in k]
    logging.error("Aucun token trouvé. Clés visibles: %s", ", ".join(sorted(seen)) or "aucune")
    raise RuntimeError("DISCORD_TOKEN manquant. Ajoute la variable dans Railway > Service > Variables")
# ─────────────────────── ROLE ─────────────────────
class PlayerTypeView(discord.ui.View):
    """
    Boutons de rôles :
      - Plateformes (PC/Consoles/Mobile) : exclusifs entre eux
      - Notifications : toggle indépendant (coexiste avec n'importe quelle plateforme)
    """
    def __init__(self):
        super().__init__(timeout=None)  # Vue persistante

    # ── Plateformes (exclusives) ─────────────────────────────────────────
    @discord.ui.button(label="💻 PC", style=discord.ButtonStyle.primary, custom_id="role_pc")
    async def btn_pc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_platform_role(interaction, ROLE_PC, "PC")

    @discord.ui.button(label="🎮 Consoles", style=discord.ButtonStyle.primary, custom_id="role_console")
    async def btn_console(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_platform_role(interaction, ROLE_CONSOLE, "Consoles")

    @discord.ui.button(label="📱 Mobile", style=discord.ButtonStyle.primary, custom_id="role_mobile")
    async def btn_mobile(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_platform_role(interaction, ROLE_MOBILE, "Mobile")

    # ── Notifications (toggle indépendant) ───────────────────────────────
    @discord.ui.button(label="🔔 Notifications", style=discord.ButtonStyle.secondary, custom_id="role_notify")
    async def btn_notify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_role(interaction, ROLE_NOTIFICATION, "Notifications")

    # ── Helpers ──────────────────────────────────────────────────────────
    async def _set_platform_role(self, interaction: discord.Interaction, role_id: int, label: str):
        """
        Règle de gestion (nouvelle) :
          - Si le membre a DÉJÀ cette plateforme -> ne rien faire (pas de toggle off).
          - Sinon -> ajouter cette plateforme et retirer automatiquement les AUTRES plateformes.
          - Le rôle 🔔 Notifications n’est JAMAIS touché ici.
        """
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ Action impossible en message privé.", ephemeral=True)

        role = guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message(f"❌ Rôle introuvable ({label}).", ephemeral=True)

        member = interaction.user
        try:
            # a) S'il a déjà cette plateforme -> NO-OP (aucun retrait)
            if role in member.roles:
                return await interaction.response.send_message(
                    f"✅ Tu es déjà sur **{label}** (aucun changement).", ephemeral=True
                )

            # b) Sinon -> ajouter cette plateforme et retirer les autres plateformes
            other_platform_ids = {ROLE_PC, ROLE_CONSOLE, ROLE_MOBILE} - {role_id}
            other_platform_roles = [guild.get_role(rid) for rid in other_platform_ids]
            remove_list = [r for r in other_platform_roles if r and r in member.roles]

            if remove_list:
                await member.remove_roles(*remove_list, reason=f"Changement de plateforme -> {label}")

            await member.add_roles(role, reason=f"Ajout rôle plateforme {label}")

            removed_txt = f" (retiré: {', '.join(f'**{r.name}**' for r in remove_list)})" if remove_list else ""
            await interaction.response.send_message(
                f"✅ Plateforme mise à jour : **{label}**{removed_txt}.\n"
                f"🔔 *Le rôle Notifications est conservé.*",
                ephemeral=True
            )

        except Exception as e:
            logging.error(f"Erreur set_platform {label}: {e}")
            await interaction.response.send_message("❌ Impossible de modifier tes rôles.", ephemeral=True)

    async def _toggle_role(self, interaction: discord.Interaction, role_id: int, label: str):
        """
        Toggle simple (utilisé pour 🔔 Notifications) : ajoute/retire UNIQUEMENT ce rôle.
        """
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ Action impossible en message privé.", ephemeral=True)

        role = guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message(f"❌ Rôle introuvable ({label}).", ephemeral=True)

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"Retrait rôle {label}")
                await interaction.response.send_message(f"🔕 Rôle **{label}** retiré.", ephemeral=True)
            else:
                await member.add_roles(role, reason=f"Ajout rôle {label}")
                await interaction.response.send_message(f"🔔 Rôle **{label}** ajouté.", ephemeral=True)
        except Exception as e:
            logging.error(f"Erreur toggle rôle {label}: {e}")
            await interaction.response.send_message("❌ Impossible de modifier tes rôles.", ephemeral=True)

# ─────────────────────── PERSISTANCE (VOLUME) ─────────────────────
# Monte un volume Railway sur /app/data (Settings → Attach Volume → mount path: /app/data)
DATA_DIR = os.getenv("DATA_DIR", "/app/data")  # tu peux aussi définir DATA_DIR=/app/data dans les variables Railway

XP_FILE = f"{DATA_DIR}/data.json"
BACKUP_FILE = f"{DATA_DIR}/backup.json"
DAILY_STATS_FILE = f"{DATA_DIR}/daily_stats.json"

def ensure_data_dir():
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

def _safe_read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def save_json(path: str, data: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

def load_json(path: str) -> dict:
    return _safe_read_json(path)

def load_daily_stats() -> dict:
    return load_json(DAILY_STATS_FILE)

def save_daily_stats(d: dict):
    save_json(DAILY_STATS_FILE, d)

# --- XP cache en mémoire (moins d'I/O, thread-safe via lock) ---
XP_CACHE: dict[str, dict] = {}
XP_LOCK = asyncio.Lock()

def _disk_load_xp() -> dict:
    ensure_data_dir()
    path = Path(XP_FILE)
    backup_path = Path(BACKUP_FILE)
    try:
        if not path.exists():
            # si rien n'existe mais un backup est présent, on restaure
            if backup_path.exists():
                data = _safe_read_json(BACKUP_FILE)
                save_json(XP_FILE, data)
                logging.info("📦 XP restauré depuis backup.json (fichier principal manquant).")
                return data
            # sinon on init un fichier vide
            save_json(XP_FILE, {})
            logging.info("📁 Fichier XP créé (vide).")
            return {}
        # lecture normale
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("⚠️ data.json corrompu, tentative de restauration depuis backup.json…")
        if backup_path.exists():
            try:
                data = json.loads(backup_path.read_text(encoding="utf-8"))
                save_json(XP_FILE, data)
                logging.info("✅ Restauration réussie depuis backup.json.")
                return data
            except Exception as e:
                logging.error(f"❌ Lecture backup impossible: {e}")
        else:
            logging.error("❌ Aucun backup disponible.")
        return {}

def _disk_save_xp(data: dict):
    ensure_data_dir()
    # on écrit d'abord le principal…
    save_json(XP_FILE, data)
    # …puis on met à jour le backup (copie 1:1)
    try:
        Path(BACKUP_FILE).write_text(Path(XP_FILE).read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as e:
        logging.error(f"❌ Écriture backup échouée: {e}")

async def xp_bootstrap_cache():
    global XP_CACHE
    XP_CACHE = _disk_load_xp()
    logging.info("🎒 XP cache chargé (%d utilisateurs).", len(XP_CACHE))

async def xp_flush_cache_to_disk():
    async with XP_LOCK:
        _disk_save_xp(XP_CACHE)
        logging.info("💾 XP flush vers disque (%d utilisateurs).", len(XP_CACHE))

# (optionnel) tu peux remplacer ta tâche auto_backup_xp par une version plus fréquente
async def auto_backup_xp(interval_seconds: int = 600):  # toutes les 10 min
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await xp_flush_cache_to_disk()
            logging.info("🛟 Sauvegarde périodique effectuée.")
        except Exception as e:
            logging.error(f"❌ Erreur sauvegarde périodique: {e}")
        await asyncio.sleep(interval_seconds)


# ─────────────────────── HELPERS ────────────────────────────
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

def _base_name_from_channel(ch: discord.VoiceChannel) -> str:
    """Extrait le 'base' depuis le nom (PC/Crossplay/Consoles/Chat [n])."""
    m = VOC_PATTERN.match(ch.name)
    if not m:
        # fallback : premier mot jusqu'au premier '•'
        return ch.name.split("•", 1)[0].strip()
    return m.group(1)

def _target_name(base: str, game: str | None) -> str:
    if AUTO_RENAME_ENABLED and game:
        name = AUTO_RENAME_FORMAT.format(base=base, game=game.strip())
        return name[:100]  # hard cap Discord
    return base

def _count_temp_vc_in_category(cat: discord.CategoryChannel) -> int:
    return sum(1 for ch in cat.voice_channels if ch.id in TEMP_VC_IDS)

# Anti-spam renommage
_last_rename_at: dict[int, float] = {}  # channel_id -> timestamp
def _can_rename(ch_id: int) -> bool:
    ts = _last_rename_at.get(ch_id, 0)
    now = asyncio.get_event_loop().time()
    if now - ts < AUTO_RENAME_COOLDOWN_SEC:
        return False
    _last_rename_at[ch_id] = now
    return True

# ── AJOUTE EN HAUT (près des autres états) ─────────────────
_rename_state: dict[int, tuple[str, int, str | None]] = {}  # ch_id -> (name, members, game)

# ── REMPLACE ta fonction par ceci ───────────────────────────
async def maybe_rename_channel_by_game(ch: discord.VoiceChannel, *, wait_presences: bool = False):
    """Renomme le salon selon le jeu majoritaire (ActivityType.playing)."""
    if not AUTO_RENAME_ENABLED or not isinstance(ch, discord.VoiceChannel):
        return

    if wait_presences:
        # la présence met parfois ~1–2s à se propager après un move/connexion
        await asyncio.sleep(2)

    base = _base_name_from_channel(ch)

    # Salon vide → reset du nom de base
    if not ch.members:
        if ch.name != base and _can_rename(ch.id):
            try:
                await ch.edit(name=base, reason="Auto-rename: salon vide, reset base")
                logging.debug(f"[auto-rename] reset -> {base} (ch={ch.id})")
            except Exception as e:
                logging.debug(f"[auto-rename] reset failed: {e}")
        _rename_state[ch.id] = (base, 0, None)
        return

    # Détection du jeu majoritaire (type Playing)
    counts: dict[str, int] = {}
    for m in ch.members:
        acts = list(getattr(m, "activities", []) or [])
        single = getattr(m, "activity", None)
        if single and single not in acts:
            acts.append(single)

        for act in acts:
            name = getattr(act, "name", None)
            atype = getattr(act, "type", None)
            if not name:
                continue
            # ne compter que les activités "Playing" (ignorer Spotify, Streaming, Custom, etc.)
            if atype == discord.ActivityType.playing:
                nm = name.strip()
                if nm:
                    counts[nm] = counts.get(nm, 0) + 1
                break  # on s'arrête à la première activité "Playing" trouvée

    game = max(counts, key=counts.get) if counts else None
    target = _target_name(base, game)
    members_count = len(ch.members)

    prev = _rename_state.get(ch.id)
    changed = (prev is None or prev[0] != ch.name or prev[1] != members_count or prev[2] != game)

    if target != ch.name and _can_rename(ch.id):
        try:
            await ch.edit(name=target, reason=f"Auto-rename: jeu détecté = {game or 'aucun'}")
        except Exception as e:
            logging.debug(f"[auto-rename] rename failed: {e}")

    if changed:
        logging.debug(
            "[auto-rename] ch=%s base='%s' game='%s' target='%s' members=%d",
            ch.id, base, game or "None", target, members_count
        )
    _rename_state[ch.id] = (ch.name, members_count, game)

# ── RÉCOMPENSES NIVEAU ─────────────────────────────────────
async def grant_level_roles(member: discord.Member, new_level: int) -> int | None:
    """Donne le rôle correspondant au plus HAUT palier atteint. Retourne l'ID du rôle donné (ou None)."""
    if not LEVEL_ROLE_REWARDS:
        return None

    eligible = [lvl for lvl in LEVEL_ROLE_REWARDS.keys() if new_level >= lvl]
    if not eligible:
        return None

    best_lvl = max(eligible)
    role_id = LEVEL_ROLE_REWARDS[best_lvl]
    role = member.guild.get_role(role_id)
    if not role:
        logging.warning(f"Role reward introuvable: {role_id}")
        return None

    # Ajoute le rôle si nécessaire
    try:
        if role not in member.roles:
            await member.add_roles(role, reason=f"Palier atteint: niveau {new_level}")
    except Exception as e:
        logging.error(f"Impossible d'ajouter le rôle {role_id} à {member}: {e}")
        return None

    # Optionnel: retirer les anciens paliers
    if REMOVE_LOWER_TIER_ROLES:
        try:
            lower_roles = [member.guild.get_role(LEVEL_ROLE_REWARDS[l])
                           for l in LEVEL_ROLE_REWARDS.keys() if l < best_lvl]
            lower_roles = [r for r in lower_roles if r and r in member.roles]
            if lower_roles:
                await member.remove_roles(*lower_roles, reason="Nouveau palier atteint")
        except Exception as e:
            logging.error(f"Impossible de retirer les anciens rôles à {member}: {e}")

    return role_id

# ── ANNONCE LEVEL-UP (NOUVEAU STYLE, SANS IMAGE) ───────────
async def announce_level_up(guild: discord.Guild, member: discord.Member, old_level: int, new_level: int, xp: int):
    """Annonce un level-up dans le salon niveaux: propre, sans image de carte, avec rôle auto."""
    channel = guild.get_channel(LEVEL_UP_CHANNEL)
    if not isinstance(channel, discord.TextChannel):
        logging.warning("Salon niveaux introuvable ou invalide.")
        return

    # Progression vers le prochain niveau
    xp_needed = (new_level + 1) ** 2 * 100
    remaining = max(0, xp_needed - xp)
    ratio = 0 if xp_needed == 0 else min(1, max(0, xp / xp_needed))

    # Barre de progression en texte (20 blocs)
    total_blocks = 20
    filled = int(ratio * total_blocks)
    bar = "▰" * filled + "▱" * (total_blocks - filled)

    # Rôle de récompense éventuel
    new_role_id = await grant_level_roles(member, new_level)
    role_line = f"\n🎖️ Nouveau rôle : <@&{new_role_id}>" if new_role_id else ""

    embed = discord.Embed(
        title="🚀 Niveau up !",
        description=(
            f"**{member.mention}** passe de **{old_level} ➜ {new_level}**.{role_line}\n\n"
            f"**Progression :** `{bar}`\n"
            f"**XP :** {xp} / {xp_needed}  •  **Reste :** {remaining} XP"
        ),
        color=0x33D17A,
        timestamp=datetime.now(PARIS_TZ)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="GG 🎉")

    try:
        await channel.send(content=f"🎉 {member.mention}", embed=embed)
    except Exception as e:
        logging.error(f"Annonce level-up échouée: {e}")

def next_vc_name(guild: discord.Guild, base: str) -> str:
    nums = []
    for ch in guild.voice_channels:
        m = VOC_PATTERN.match(ch.name)
        if m and m.group(1).lower() == base.lower():
            n = int(m.group(2)) if m.group(2) else 1
            nums.append(n)
    n = (max(nums) + 1) if nums else 1
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
    stats = load_daily_stats()
    g = str(guild_id)
    date_key = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
    stats.setdefault(g, {}).setdefault(date_key, {}).setdefault(str(user_id), {"msg": 0, "voice_min": 0})
    stats[g][date_key][str(user_id)]["msg"] += msg_inc
    stats[g][date_key][str(user_id)]["voice_min"] += voice_min_inc
    save_daily_stats(stats)

# ─────────────────────── XP PUBLIC API (pour cogs) ───────────────────────
async def award_xp(user_id: int, amount: int) -> tuple[int, int, int]:
    """
    Ajoute 'amount' d'XP à user_id dans le cache global.
    Retourne (old_level, new_level, total_xp_après).
    Ne fait PAS d'annonce ici (le cog peut le faire s'il veut).
    """
    uid = str(user_id)

    # No-op cohérent si amount <= 0
    if amount <= 0:
        async with XP_LOCK:
            data = XP_CACHE.get(uid, {"xp": 0, "level": 0})
            old_level = int(data.get("level", 0))
            new_level = old_level
            total_xp = int(data.get("xp", 0))
            return old_level, new_level, total_xp

    async with XP_LOCK:
        user = XP_CACHE.setdefault(uid, {"xp": 0, "level": 0})
        old_level = int(user.get("level", 0))
        user["xp"] = int(user.get("xp", 0)) + int(amount)
        new_level = get_level(int(user["xp"]))
        if new_level > old_level:
            user["level"] = new_level
        return old_level, new_level, int(user["xp"])

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
@bot.tree.command(name="type_joueur", description="Choisir PC, Console ou Mobile")
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
        "🔗 Voici le lien pour rejoindre notre serveur :\nhttps://discord.gg/yB7Ekc4GKM",
        ephemeral=False
    )

# 🧪 MESSAGE D'ESSAI DANS LE SALON NIVEAUX (owner-only)
@bot.tree.command(name="test_niveau", description="Tester l'annonce de level-up (réservé au propriétaire)")
@app_commands.describe(
    niveau="(Optionnel) Test simple: nouveau niveau à simuler",
    membre="(Optionnel) Membre ciblé (par défaut: toi)",
    ancien_niveau="(Optionnel) Ancien niveau (défaut 4)",
    nouveau_niveau="(Optionnel) Nouveau niveau (défaut 5)",
    xp="(Optionnel) XP actuel; par défaut = seuil du nouveau niveau"
)
async def test_niveau(
    interaction: discord.Interaction,
    niveau: int | None = None,
    membre: discord.Member | None = None,
    ancien_niveau: app_commands.Range[int, 0, 999] = 4,
    nouveau_niveau: app_commands.Range[int, 1, 1000] = 5,
    xp: app_commands.Range[int, 0, 10_000_000] | None = None
):
    # Restriction propriétaire (utiliser OWNER_ID)
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Commande réservée au propriétaire.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # Mode simple: /test_niveau niveau:12
    if niveau is not None:
        if niveau <= 0:
            await interaction.followup.send("❌ Le niveau doit être > 0.", ephemeral=True)
            return
        member = interaction.user
        old_lvl = max(0, niveau - 1)
        new_lvl = niveau
        xp_val = xp if xp is not None else (new_lvl ** 2 * 100)
    else:
        # Mode avancé: membre/anciens/nouveaux/xp
        member = membre or interaction.user
        if nouveau_niveau <= ancien_niveau:
            await interaction.followup.send("❌ Le nouveau niveau doit être supérieur à l'ancien.", ephemeral=True)
            return
        old_lvl = ancien_niveau
        new_lvl = nouveau_niveau
        xp_val = xp if xp is not None else (new_lvl ** 2 * 100)

    try:
        await announce_level_up(interaction.guild, member, old_lvl, new_lvl, xp_val)
        await interaction.followup.send("✅ Message d'essai envoyé dans le salon niveaux.", ephemeral=True)
    except Exception as e:
        logging.error(f"/test_niveau échec: {e}")
        await interaction.followup.send("❌ Impossible d'envoyer le message d'essai.", ephemeral=True)

@bot.tree.command(name="rang", description="Affiche ton niveau avec une carte graphique")
async def rang(interaction: discord.Interaction):
    # Defer pour le spinner (et éviter timeout)
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass

    user_id = str(interaction.user.id)
    async with XP_LOCK:
        data = XP_CACHE.get(user_id)
        if not data:
            await interaction.followup.send("Tu n'as pas encore de niveau... Commence à discuter !", ephemeral=True)
            return
        level = int(data.get("level", 0))
        xp = int(data.get("xp", 0))
        xp_next = (level + 1) ** 2 * 100

    try:
        image = await generate_rank_card(interaction.user, level, xp, xp_next)
        file = discord.File(fp=image, filename="rank.png")
        try:
            await interaction.followup.send(file=file, ephemeral=True)
        except discord.Forbidden:
            pass
        except discord.HTTPException as e:
            logging.warning(f"/rang: envoi ephemeral échoué, fallback public. Raison: {e}")
            await interaction.channel.send(
                content=f"{interaction.user.mention} voici ta carte de niveau :",
                file=file
            )
            await interaction.followup.send(
                "Je n'ai pas pu l'envoyer en privé, je l'ai postée dans le salon.",
                ephemeral=True
            )
    except ImportError as e:
        logging.exception(f"/rang: ImportError (Pillow manquante ?) {e}")
        await interaction.followup.send("❌ Erreur interne: dépendance manquante (Pillow).", ephemeral=True)
    except Exception as e:
        logging.exception(f"/rang: exception inattendue: {e}")
        await interaction.followup.send("❌ Une erreur est survenue pendant la génération de la carte.", ephemeral=True)

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

    me = (interaction.guild.me or interaction.guild.get_member(bot.user.id))
    if not me:
        await interaction.followup.send("❌ Impossible de vérifier mes permissions.", ephemeral=True); return
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
@bot.tree.command(name="invitation", description="Créer une session pour chercher des joueurs")
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
async def invitation(interaction: discord.Interaction, jeu: str, plateforme: app_commands.Choice[str], heure: str, categorie: app_commands.Choice[str]):
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
async def ensure_vc_buttons_message():
    """
    Ré-attache ou republie le message permanent avec la vue de création de salons vocaux.
    """
    await bot.wait_until_ready()
    channel = bot.get_channel(LOBBY_TEXT_CHANNEL)
    if not isinstance(channel, discord.TextChannel):
        logging.warning(f"❌ Salon lobby introuvable: {LOBBY_TEXT_CHANNEL}")
        return

    view = VCButtonView()
    found = None

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

async def ensure_roles_buttons_message():
    """
    (Re)poste le message permanent des rôles PC/Consoles/Mobile/Notifications
    dans CHANNEL_ROLES et (ré)attache la vue PlayerTypeView.
    """
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ROLES)
    if not isinstance(channel, discord.TextChannel):
        logging.warning(f"❌ Salon des rôles introuvable: {CHANNEL_ROLES}")
        return

    view = PlayerTypeView()
    found = None

    # on cherche un ancien message marqué pour l’éditer
    try:
        async for msg in channel.history(limit=100):
            if msg.author == bot.user and ROLES_PERMA_MESSAGE_MARK in (msg.content or ""):
                found = msg
                break
    except Exception as e:
        logging.error(f"Erreur lecture historique (roles): {e}")

    content = (
        f"{ROLES_PERMA_MESSAGE_MARK}\n"
        "🎮 **Choisis ta plateforme** (exclusives) **et** active les notifications si tu veux être ping :\n"
        "• 💻 PC\n"
        "• 🎮 Consoles\n"
        "• 📱 Mobile\n"
        "• 🔔 Notifications *(ajout/retrait **indépendant**, conservé quand tu changes de plateforme)*"
    )

    if found:
        try:
            await found.edit(content=content, view=view)
            logging.info("🔁 Message rôles réattaché (avec vue).")
            return
        except Exception as e:
            logging.error(f"Échec réattachement des rôles, je reposte un nouveau message: {e}")

    try:
        await channel.send(content, view=view)
        logging.info("📌 Message rôles publié (nouveau).")
    except Exception as e:
        logging.error(f"Erreur envoi message rôles: {e}")

async def reminder_loop_24h():
    await bot.wait_until_ready()
    while not bot.is_closed():
        today = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
        for guild in bot.guilds:
            channel = guild.get_channel(CHANNEL_ROLES)
            if not isinstance(channel, discord.TextChannel):
                continue

            me = guild.me or guild.get_member(bot.user.id)
            if not me or not channel.permissions_for(me).send_messages:
                continue

            sent = 0
            for member in guild.members:
                if member.bot:
                    continue
                if len(member.roles) <= 1:
                    key = (guild.id, member.id)
                    last = REMINDER_LAST.get(key)
                    if last == today:
                        continue
                    try:
                        await channel.send(
                            f"{member.mention} tu n’as pas encore choisi ton rôle ici. "
                            "Clique sur un bouton pour sélectionner ta plateforme 💻🎮📱"
                        )
                        REMINDER_LAST[key] = today
                        sent += 1
                        if sent >= REMINDER_DAILY_CAP_PER_GUILD:
                            break
                    except Exception as e:
                        logging.error(f"Erreur rappel rôles {member} ({guild.id}): {e}")
        await asyncio.sleep(86400)

async def daily_summary_loop():
    """
    Chaque minuit (Europe/Paris), poste le résumé d’activité du jour écoulé pour CHAQUE serveur.
    """
    await bot.wait_until_ready()
    while not bot.is_closed():
        # dormir jusqu'au prochain minuit (heure locale Paris)
        now = datetime.now(PARIS_TZ)
        tomorrow = now + timedelta(days=1)
        next_midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep(max(1, (next_midnight - now).total_seconds()))

        # après le réveil, on recalcule "aujourd'hui" et on cible "hier"
        today = datetime.now(PARIS_TZ).date()
        yesterday = today - timedelta(days=1)
        day_key = yesterday.strftime("%Y-%m-%d")

        stats = load_daily_stats()

        for guild in bot.guilds:
            gkey = str(guild.id)
            gstats = stats.get(gkey, {}).get(day_key, {})
            if not gstats:
                continue

            def u_name(uid: str) -> str:
                member = guild.get_member(int(uid))
                return member.display_name if member else f"User {uid}"

            items = []
            for uid, data in gstats.items():
                msgs = int(data.get("msg", 0))
                vmin = int(data.get("voice_min", 0))
                score = msgs + vmin
                items.append((uid, msgs, vmin, score))

            top_msg  = sorted(items, key=lambda x: x[1], reverse=True)[:5]
            top_vc   = sorted(items, key=lambda x: x[2], reverse=True)[:5]
            top_mvp  = sorted(items, key=lambda x: x[3], reverse=True)[:5]

            total_msgs = sum(x[1] for x in items)
            total_vmin = sum(x[2] for x in items)

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

            ch = guild.get_channel(ACTIVITY_SUMMARY_CH)
            if isinstance(ch, discord.TextChannel):
                try:
                    me = guild.me or guild.get_member(bot.user.id)
                    if not ch.permissions_for(me).mention_everyone:
                        await ch.send("⚠️ Je n'ai pas la permission de mentionner @everyone ici.")
                        content = "Voici les joueurs les plus actifs d'hier !"
                        await ch.send(content=content, embed=embed)
                    else:
                        await ch.send(content="@everyone — Voici les joueurs les plus actifs d'hier !",
                                      embed=embed,
                                      allowed_mentions=discord.AllowedMentions(everyone=True))
                except Exception as e:
                    logging.error(f"Envoi résumé quotidien échoué (guild {guild.id}): {e}")

        # nettoyage des stats > 14 jours
        try:
            cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
            for gkey in list(stats.keys()):
                for dkey in list(stats.get(gkey, {}).keys()):
                    if dkey < cutoff:
                        stats[gkey].pop(dkey, None)
                if not stats[gkey]:
                    stats.pop(gkey, None)
            save_daily_stats(stats)
        except Exception as e:
            logging.error(f"Purge stats anciennes échouée: {e}")

async def weekly_summary_loop():
    """Chaque lundi 00:05 (Europe/Paris), poste le résumé hebdo (semaine précédente lun→dim)."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.now(PARIS_TZ)

        # calcule le prochain lundi 00:05
        days_ahead = (7 - now.weekday()) % 7  # 0=lundi
        if days_ahead == 0 and (now.hour, now.minute) >= (0, 5):
            days_ahead = 7
        next_run = (now + timedelta(days=days_ahead)).replace(hour=0, minute=5, second=0, microsecond=0)
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))

        # après le réveil : calcul de la semaine précédente (lundi→dimanche)
        today = datetime.now(PARIS_TZ).date()            # on est lundi (date du "run")
        last_sunday = today - timedelta(days=today.weekday() + 1)   # hier (dimanche)
        last_monday = last_sunday - timedelta(days=6)               # lundi précédent

        stats = load_daily_stats()

        for guild in bot.guilds:
            gkey = str(guild.id)

            # agrège la semaine
            items = {}  # uid -> {"msg":int,"voice_min":int}
            day = last_monday
            while day <= last_sunday:
                day_key = day.strftime("%Y-%m-%d")
                gstats = stats.get(gkey, {}).get(day_key, {})
                for uid, data in gstats.items():
                    entry = items.setdefault(uid, {"msg": 0, "voice_min": 0})
                    entry["msg"] += int(data.get("msg", 0))
                    entry["voice_min"] += int(data.get("voice_min", 0))
                day += timedelta(days=1)

            if not items:
                continue

            def u_name(uid: str) -> str:
                m = guild.get_member(int(uid))
                return m.display_name if m else f"User {uid}"

            table = [(uid, v["msg"], v["voice_min"], v["msg"] + v["voice_min"]) for uid, v in items.items()]
            top_msg = sorted(table, key=lambda x: x[1], reverse=True)[:5]
            top_vc  = sorted(table, key=lambda x: x[2], reverse=True)[:5]
            top_mvp = sorted(table, key=lambda x: x[3], reverse=True)[:5]

            total_msgs = sum(x[1] for x in table)
            total_vmin = sum(x[2] for x in table)

            period_text = f"{last_monday.strftime('%Y-%m-%d')} → {last_sunday.strftime('%Y-%m-%d')}"
            embed = discord.Embed(
                title=f"🏁 Résumé hebdo — {period_text}",
                description=f"**Totaux** : {total_msgs} messages • {total_vmin} min en vocal",
                color=0x5865F2
            )

            def medal(i): return "🥇" if i==0 else ("🥈" if i==1 else ("🥉" if i==2 else f"{i+1}."))

            if top_msg:
                embed.add_field(
                    name="💬 Top Messages",
                    value="\n".join([f"**{medal(i)}** {u_name(uid)} — {msgs} msgs"
                                     for i, (uid, msgs, _, _) in enumerate(top_msg)]),
                    inline=False
                )
            if top_vc:
                embed.add_field(
                    name="🎙️ Top Vocal",
                    value="\n".join([f"**{medal(i)}** {u_name(uid)} — {vmin} min"
                                     for i, (uid, _, vmin, _) in enumerate(top_vc)]),
                    inline=False
                )
            if top_mvp:
                embed.add_field(
                    name="🏆 MVP (msgs + min)",
                    value="\n".join([f"**{medal(i)}** {u_name(uid)} — {score} pts"
                                     for i, (uid, _, __, score) in enumerate(top_mvp)]),
                    inline=False
                )

            ch = guild.get_channel(ACTIVITY_SUMMARY_CH)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.send(content="@everyone — Podium de la semaine !",
                                  embed=embed,
                                  allowed_mentions=discord.AllowedMentions(everyone=True))
                except Exception as e:
                    logging.error(f"Envoi résumé hebdo échoué (guild {guild.id}): {e}")

async def auto_rename_poll():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for vc_id in list(TEMP_VC_IDS):
                ch = bot.get_channel(vc_id)
                if isinstance(ch, discord.VoiceChannel):
                    await maybe_rename_channel_by_game(ch)
        except Exception as e:
            logging.debug(f"auto_rename_poll error: {e}")
        await asyncio.sleep(20)  # toutes les 20s

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
        guild = interaction.guild
        me = guild.me or guild.get_member(bot.user.id)
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

        # 🔒 Limite par catégorie
        limit = TEMP_VC_LIMITS.get(category.id)
        if limit is not None:
            current = _count_temp_vc_in_category(category)
            if current >= limit:
                await safe_respond(
                    interaction,
                    f"⛔ Limite atteinte : il y a déjà **{current}/{limit}** salons temporaires dans **{category.name}**.",
                    ephemeral=True
                )
                return

        try:
            vc = await guild.create_voice_channel(
                name=name, category=category,
                reason=f"Salon temporaire ({profile}) demandé par {interaction.user}",
            )
            TEMP_VC_IDS.add(vc.id)
            # Auto-rename initial (si des joueurs sont déjà dedans après move)
            if interaction.user.voice and interaction.user.voice.channel:
                await interaction.user.move_to(vc, reason="Création de salon temporaire")
                moved_text = f"Tu as été déplacé dans **{vc.name}**."
            else:
                moved_text = f"Rejoins **{vc.name}** quand tu veux."

            await safe_respond(interaction, f"✅ Salon **{vc.name}** créé. {moved_text}\n_Ce salon sera supprimé lorsqu'il sera vide._", ephemeral=True)

            # Tente une première mise à jour du nom (si un jeu est détecté)
            await maybe_rename_channel_by_game(vc)

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
@bot.tree.command(name="roles_refresh", description="Reposter/reattacher le message des rôles (réservé au propriétaire)")
async def roles_refresh(interaction: discord.Interaction):
    if interaction.user.id != 541417878314942495:
        return await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    await ensure_roles_buttons_message()
    await interaction.followup.send("✅ Message de rôles (re)publié/reattaché.", ephemeral=True)

@bot.event
async def on_ready():
    # (optionnel) chunker les guilds pour précharger les membres si intents activés dans le portail
    try:
        for g in bot.guilds:
            await g.chunk()
    except Exception as e:
        logging.debug(f"chunk failed: {e}")

    logging.info(f"✅ Connecté en tant que {bot.user} (latence {bot.latency*1000:.0f} ms)")

@bot.event
async def on_message(message: discord.Message):
    # Filtrage bot/DM
    if message.author.bot or not message.guild:
        return

    # Slash commands ne passent pas par on_message.
    # Si commande prefix "!", on laisse discord.py gérer et on ne crédite pas d'XP.
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # Crédit XP messages (via cache)
    user_id = str(message.author.id)
    async with XP_LOCK:
        user = XP_CACHE.setdefault(user_id, {"xp": 0, "level": 0})
        user["xp"] += MSG_XP
        old_level = int(user.get("level", 0))
        new_level = get_level(int(user["xp"]))

        # Met à jour le niveau dans le cache
        if new_level > old_level:
            user["level"] = new_level

    # Annonce level-up (hors lock)
    if new_level > old_level:
        try:
            await announce_level_up(message.guild, message.author, old_level, new_level, int(user["xp"]))
        except Exception as e:
            logging.error(f"Erreur envoi annonce niveau : {e}")

    # Stats quotidiennes (messages)
    incr_daily_stat(message.guild.id, message.author.id, msg_inc=1)

    # Laisse la possibilité d'autres commandes prefix
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
    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        await maybe_rename_channel_by_game(before.channel, wait_presences=True)

    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        await maybe_rename_channel_by_game(after.channel, wait_presences=True)

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
                gained_xp = minutes_spent * VOICE_XP_PER_MIN
                async with XP_LOCK:
                    user = XP_CACHE.setdefault(user_id, {"xp": 0, "level": 0})
                    user["xp"] += gained_xp
                    old_level = int(user.get("level", 0))
                    new_level = get_level(int(user["xp"]))
                    if new_level > old_level:
                        user["level"] = new_level
                if new_level > old_level:
                    try:
                        await announce_level_up(member.guild, member, old_level, new_level, int(user["xp"]))
                    except Exception as e:
                        logging.error(f"Erreur annonce niveau vocal : {e}")
                incr_daily_stat(member.guild.id, member.id, voice_min_inc=minutes_spent)

    # Changement de salon
    elif before.channel and after.channel and before.channel != after.channel:
        joined_at = voice_times.get(user_id)
        if joined_at:
            seconds_spent = (datetime.utcnow() - joined_at).total_seconds()
            minutes_spent = int(seconds_spent // 60)
            if minutes_spent >= 1:
                gained_xp = minutes_spent * VOICE_XP_PER_MIN
                async with XP_LOCK:
                    user = XP_CACHE.setdefault(user_id, {"xp": 0, "level": 0})
                    user["xp"] += gained_xp
                    old_level = int(user.get("level", 0))
                    new_level = get_level(int(user["xp"]))
                    if new_level > old_level:
                        user["level"] = new_level
                if new_level > old_level:
                    try:
                        await announce_level_up(member.guild, member, old_level, new_level, int(user["xp"]))
                    except Exception as e:
                        logging.error(f"Erreur annonce niveau vocal (move): {e}")
                incr_daily_stat(member.guild.id, member.id, voice_min_inc=minutes_spent)

        # redémarre le chrono pour le nouveau salon
        voice_times[user_id] = datetime.utcnow()

    # Suppression des salons temporaires vides
    if before.channel and before.channel.id in TEMP_VC_IDS and not before.channel.members:
        try:
            await before.channel.delete(reason="Salon temporaire vide")
            TEMP_VC_IDS.discard(before.channel.id)
        except Exception as e:
            logging.error(f"Suppression VC temporaire échouée: {e}")
    # ... après avoir géré les minutes/XP etc.

# ─────────────────────── SETUP ─────────────────────────────
async def _setup_hook():
    await xp_bootstrap_cache()

    # Exposer API pour les cogs
    bot.award_xp = award_xp
    bot.announce_level_up = announce_level_up

    bot.add_view(VCButtonView())
    bot.add_view(LiveTikTokView())
    bot.add_view(PlayerTypeView())

    asyncio.create_task(reminder_loop_24h())
    asyncio.create_task(auto_backup_xp())
    asyncio.create_task(ensure_vc_buttons_message())
    asyncio.create_task(ensure_roles_buttons_message())
    asyncio.create_task(daily_summary_loop())   # Résumé quotidien
    asyncio.create_task(weekly_summary_loop())  # Résumé hebdo
    asyncio.create_task(auto_rename_poll())

    # 🔽 CHARGER L’EXTENSION AVANT LA SYNC (et l’await !)
    try:
        await bot.load_extension("cogs.roulette")
        logging.info("🎰 Extension cogs.roulette chargée.")
    except Exception as e:
        logging.error(f"❌ Impossible de charger cogs.roulette: {e}")

    # 🔽 SYNC APRÈS CHARGEMENT DES EXTENSIONS
    try:
        await bot.tree.sync()
        logging.info("🌐 Slash commands synchronisées.")
    except Exception as e:
        logging.error(f"❌ Échec sync des slash commands: {e}")

bot.setup_hook = _setup_hook

# ─────────────────────── MAIN ──────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
