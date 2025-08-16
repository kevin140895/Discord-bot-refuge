import asyncio
import io
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import PermissionOverwrite, app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from imageio_ffmpeg import get_ffmpeg_exe
import yt_dlp
from utils.discord_utils import ensure_channel_has_message
from utils.temp_vc_cleanup import delete_untracked_temp_vcs
from utils.interactions import safe_respond
from storage.temp_vc_store import load_temp_vc_ids, save_temp_vc_ids
from view import (
    PlayerTypeView,
    ROLE_PC,
    ROLE_CONSOLE,
    ROLE_MOBILE,
    ROLE_NOTIFICATION,
)


# ─────────────────────── ENV & LOGGING ─────────────────────
load_dotenv(override=True)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logging.getLogger().setLevel(logging.DEBUG)

# ─────────────────────── INTENTS / BOT ─────────────────────
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Salons statistiques du serveur ─────────────────────────
STATS_CATEGORY_ID = 1406408038692294676  # Catégorie "📊 Statistiques"
STATS_CHANNELS = {
    "members": "👥 Membres",
    "online": "🟢 En ligne",
    "voice": "🎮 En vocal",
}
# ── XP CONFIG ───────────────────────────────────────────────
MSG_XP = 8  # XP par message texte
VOICE_XP_PER_MIN = 3  # XP par minute en vocal
# Formule de niveau: seuil (niveau n -> n+1) = (n+1)^2 * 100 XP
REMOVE_LOWER_TIER_ROLES = True

# ── Récompenses par niveau (requis par grant_level_roles)
LEVEL_ROLE_REWARDS = {
    5: 1403510226354700430,  # Bronze
    10: 1403510368340410550,  # Argent
    20: 1403510466818605118,  # Or
}

# ── Rôles plateformes + notifications

# (facultatif, pratique si tu veux itérer)
PLATFORM_ROLE_IDS = {
    "PC": ROLE_PC,
    "Consoles": ROLE_CONSOLE,
    "Mobile": ROLE_MOBILE,
}
TEMP_VC_CATEGORY = 1400559884117999687  # ID catégorie vocale temporaire
TEMP_VC_TEMPLATE_NAME = "template"
LOBBY_VC_ID = 1405630965803520221
RADIO_VC_ID: int = 1405695147114758245
RADIO_MUTED_ROLE_ID = 1403510368340410550  # rôle à mute dans le canal radio
RADIO_STREAM_URL = "http://stream.laut.fm/englishrap"
XP_VIEWER_ROLE_ID = 1403510368340410550  # rôle autorisé à voir l'XP serveur
TOP_MSG_ROLE_ID = 1406412171965104208  # Écrivain du Refuge
TOP_VC_ROLE_ID = 1406412383878119485   # Voix du Refuge
MVP_ROLE_ID = 1406412507433795595      # MVP du Refuge


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
# Valeur réduite pour rendre le renommage plus réactif
AUTO_RENAME_COOLDOWN_SEC = 15

# ─────────────────────── CONFIG ─────────────────────────────
LEVEL_UP_CHANNEL = 1402419913716531352
CHANNEL_ROLES = 1400560866478395512
CHANNEL_WELCOME = 1400550333796716574
LOBBY_TEXT_CHANNEL = 1402258805533970472
TIKTOK_ANNOUNCE_CH = 1400552164979507263
ACTIVITY_SUMMARY_CH = 1400552164979507263
ROULETTE_CHANNEL_ID = 1405170020748755034

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


def _is_roles_permanent_message(msg: discord.Message) -> bool:
    """Détecte le message permanent des rôles via l'ID mémorisé ou la présence des boutons rôles."""
    if msg.author != bot.user:
        return False

    remembered_id = _load_roles_perma_msg_id()
    if remembered_id and msg.id == remembered_id:
        return True

    required = {
        "role_pc",
        "role_console",
        "role_mobile",
        "role_notifications",
    }
    seen: set[str] = set()
    for comp in msg.components:
        for child in getattr(comp, "children", []):
            cid = getattr(child, "custom_id", None)
            if cid:
                seen.add(cid)
    return required.issubset(seen)


# ─────────────────────── ETATS RUNTIME ──────────────────────
# voice_times et XP sont accumulés en mémoire puis sauvegardés périodiquement
voice_times: dict[str, datetime] = {}  # user_id -> datetime d'entrée (naïf UTC)
TEMP_VC_IDS: set[int] = set(load_temp_vc_ids())  # ids des salons vocaux temporaires
AUTO_MUTED_USERS: set[int] = set()  # utilisateurs auto-mute dans le canal radio

# FFmpeg: privilégier le binaire système si présent
FFMPEG_PATH = (
    "/usr/bin/ffmpeg" if Path("/usr/bin/ffmpeg").exists() else get_ffmpeg_exe()
)
logging.info(f"[voice] Using FFmpeg at: {FFMPEG_PATH}")

# ─ Vérif/lib chargement Opus (log au démarrage) ─
try:
    import discord.opus as _opus

    tried = []
    if not _opus.is_loaded():
        for _name in ("libopus.so.0", "libopus.so", "opus"):
            try:
                _opus.load_opus(_name)
                tried.append((_name, "OK"))
                break

            except Exception as e:
                tried.append((_name, f"ERR: {e!r}"))
    if not _opus.is_loaded():
        raise RuntimeError(
            "Opus library could not be loaded. Install libopus0 (e.g. `apt install libopus0`)."
        )
    logging.info(f"[voice] Opus loaded: {_opus.is_loaded()} (tries={tried})")
except Exception as e:
    logging.warning(f"[voice] Opus check failed: {e}")
    raise

# Tâches radio
_radio_task: asyncio.Task | None = None
_radio_lock = asyncio.Lock()


# ─────────────────────── TOKEN ──────────────────────────────
TOKEN = (
    os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
)
if not TOKEN:
    seen = [k for k in os.environ.keys() if "TOKEN" in k or "DISCORD" in k]
    logging.error(
        "Aucun token trouvé. Clés visibles: %s",
        ", ".join(sorted(seen)) or "aucune",
    )
    raise RuntimeError(
        "DISCORD_TOKEN manquant. Ajoute la variable dans Railway > Service > Variables"
    )


# ─────────────────────── PERSISTANCE (VOLUME) ─────────────────────
# Monte un volume Railway sur /app/data (Settings → Attach Volume → mount path: /app/data)
DATA_DIR = os.getenv(
    "DATA_DIR", "/app/data"
)  # tu peux aussi définir DATA_DIR=/app/data dans les variables Railway

XP_FILE = f"{DATA_DIR}/data.json"
BACKUP_FILE = f"{DATA_DIR}/backup.json"
DAILY_STATS_FILE = f"{DATA_DIR}/daily_stats.json"
VOICE_TIMES_FILE = f"{DATA_DIR}/voice_times.json"


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
    Path(path).write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
    )


def load_json(path: str) -> dict:
    return _safe_read_json(path)


def load_voice_times() -> dict[str, datetime]:
    data = load_json(VOICE_TIMES_FILE)
    out: dict[str, datetime] = {}
    for uid, iso in data.items():
        try:
            out[uid] = datetime.fromisoformat(iso)
        except Exception:
            continue
    return out


def save_voice_times(d: dict[str, datetime]):
    serializable = {uid: dt.astimezone(timezone.utc).isoformat() for uid, dt in d.items()}
    save_json(VOICE_TIMES_FILE, serializable)


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
                logging.info(
                    "📦 XP restauré depuis backup.json (fichier principal manquant)."
                )
                return data
            # sinon on init un fichier vide
            save_json(XP_FILE, {})
            logging.info("📁 Fichier XP créé (vide).")
            return {}
        # lecture normale
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning(
            "⚠️ data.json corrompu, tentative de restauration depuis backup.json…"
        )
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
        Path(BACKUP_FILE).write_text(
            Path(XP_FILE).read_text(encoding="utf-8"), encoding="utf-8"
        )
    except Exception as e:
        logging.error(f"❌ Écriture backup échouée: {e}")


async def xp_bootstrap_cache():
    global XP_CACHE
    XP_CACHE = _disk_load_xp()
    logging.info("🎒 XP cache chargé (%d utilisateurs).", len(XP_CACHE))


async def xp_flush_cache_to_disk():
    async with XP_LOCK:
        _disk_save_xp(XP_CACHE)
        logging.info(
            "💾 XP flush vers disque (%d utilisateurs).", len(XP_CACHE)
        )


# (optionnel) tu peux remplacer ta tâche auto_backup_xp par une version plus fréquente
async def auto_backup_xp(interval_seconds: int = 600):  # toutes les 10 min
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await xp_flush_cache_to_disk()
            save_voice_times(voice_times)
            logging.info("🛟 Sauvegarde périodique effectuée.")
        except Exception as e:
            logging.error(f"❌ Erreur sauvegarde périodique: {e}")
        await asyncio.sleep(interval_seconds)


# ── PERSISTANCE DU MESSAGE PERMANENT VC ────────────────────
PERMA_MSG_FILE = f"{DATA_DIR}/vc_buttons_msg.json"
ROLES_PERMA_MSG_FILE = f"{DATA_DIR}/roles_buttons_msg.json"


def _load_perma_msg_id() -> int | None:
    d = _safe_read_json(PERMA_MSG_FILE)
    mid = d.get("message_id")
    if isinstance(mid, int):
        return mid
    if isinstance(mid, str) and mid.isdigit():
        return int(mid)
    return None


def _load_roles_perma_msg_id() -> int | None:
    d = _safe_read_json(ROLES_PERMA_MSG_FILE)
    mid = d.get("message_id")
    if isinstance(mid, int):
        return mid
    if isinstance(mid, str) and mid.isdigit():
        return int(mid)
    return None


def _save_roles_perma_msg_id(mid: int):
    ensure_data_dir()
    Path(ROLES_PERMA_MSG_FILE).write_text(
        json.dumps({"message_id": mid}, indent=2),
        encoding="utf-8",
    )


def _save_perma_msg_id(mid: int):
    ensure_data_dir()
    Path(PERMA_MSG_FILE).write_text(
        json.dumps({"message_id": mid}, indent=2),
        encoding="utf-8",
    )


def _is_vc_permanent_message(msg: discord.Message) -> bool:
    """Reconnaît le message permanent VC :
    - legacy: contenu contient [VC_BUTTONS_PERMANENT]
    - nouveau: présence des boutons create_vc_* (custom_id)
    """
    if msg.author != bot.user:
        return False

    # Legacy: ancien contenu avec le marqueur en clair
    if PERMA_MESSAGE_MARK in (msg.content or ""):
        return True

    # Nouveau: détecter les boutons spécifiques
    try:
        if msg.components:
            for row in msg.components:
                for comp in getattr(row, "children", []) or []:
                    cid = getattr(comp, "custom_id", "") or ""
                    if cid in {
                        "create_vc_pc",
                        "create_vc_consoles",
                        "create_vc_crossplay",
                        "create_vc_chat",
                    }:
                        return True
    except Exception:
        pass

    # Détection par description de l'embed (fallback)
    for e in msg.embeds or []:
        if (e.description or "").startswith(
            "👋 **Crée ton salon vocal temporaire**"
        ):
            return True

    return False


# ─────────────────────── HELPERS ────────────────────────────
def get_level(xp: int) -> int:
    level = 0
    while xp >= (level + 1) ** 2 * 100:
        level += 1
    return level



def is_xp_viewer(inter: discord.Interaction) -> bool:
    if inter.guild and inter.user.id == inter.guild.owner_id:
        return True
    return any(r.id == XP_VIEWER_ROLE_ID for r in getattr(inter.user, "roles", []))


async def generate_rank_card(
    user: discord.User, level: int, xp: int, xp_needed: int
):
    from PIL import Image, ImageDraw
    import io

    img = Image.new("RGB", (460, 140), color=(30, 41, 59))
    draw = ImageDraw.Draw(img)
    draw.text((16, 14), f"{user.name} — Niveau {level}", fill=(255, 255, 255))
    draw.text((16, 52), f"XP: {xp} / {xp_needed}", fill=(220, 220, 220))
    bar_x, bar_y, bar_w, bar_h = 16, 90, 428, 22
    draw.rectangle(
        [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=(71, 85, 105)
    )
    ratio = max(0.0, min(1.0, xp / max(1, xp_needed)))
    draw.rectangle(
        [bar_x, bar_y, bar_x + int(bar_w * ratio), bar_y + bar_h],
        fill=(34, 197, 94),
    )
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


async def _rebuild_temp_vc_ids() -> None:
    """Reconstruit ``TEMP_VC_IDS`` à partir des salons présents."""
    category = bot.get_channel(TEMP_VC_CATEGORY)
    if not isinstance(category, discord.CategoryChannel):
        logging.warning(
            "Catégorie vocale temporaire introuvable (%s)", TEMP_VC_CATEGORY
        )
        return
    TEMP_VC_IDS.clear()
    for ch in category.voice_channels:
        base = ch.name.split("•", 1)[0].strip()
        if VOC_PATTERN.match(base):
            TEMP_VC_IDS.add(ch.id)
    save_temp_vc_ids(TEMP_VC_IDS)


async def ensure_temp_vc_template(bot: commands.Bot) -> None:
    """Garantit la présence d'un salon vocal modèle."""
    category = bot.get_channel(TEMP_VC_CATEGORY)
    if not isinstance(category, discord.CategoryChannel):
        logging.warning(
            "Catégorie vocale temporaire introuvable (%s)", TEMP_VC_CATEGORY
        )
        return
    template = discord.utils.get(category.voice_channels, name=TEMP_VC_TEMPLATE_NAME)
    if template:
        return
    overwrites = {
        category.guild.default_role: PermissionOverwrite(connect=False, speak=False)
    }
    try:
        await category.guild.create_voice_channel(
            TEMP_VC_TEMPLATE_NAME,
            category=category,
            bitrate=96000,
            user_limit=0,
            overwrites=overwrites,
            reason="Création du template de salon vocal temporaire",
        )
    except Exception as exc:
        logging.warning("Impossible de créer le salon template: %s", exc)


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
_rename_state: dict[int, tuple[str, int, str | None]] = (
    {}
)  # ch_id -> (name, members, game)


# ── REMPLACE ta fonction par ceci ───────────────────────────
async def maybe_rename_channel_by_game(
    ch: discord.VoiceChannel, *, wait_presences: bool = False
):
    # ⛔ Ne jamais renommer le salon radio
    if getattr(ch, "id", None) == RADIO_VC_ID:
        return
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
                await ch.edit(
                    name=base, reason="Auto-rename: salon vide, reset base"
                )
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
    changed = (
        prev is None
        or prev[0] != ch.name
        or prev[1] != members_count
        or prev[2] != game
    )

    if target != ch.name and _can_rename(ch.id):
        try:
            await ch.edit(
                name=target,
                reason=f"Auto-rename: jeu détecté = {game or 'aucun'}",
            )
        except Exception as e:
            logging.debug(f"[auto-rename] rename failed: {e}")

    if changed:
        logging.debug(
            "[auto-rename] ch=%s base='%s' game='%s' target='%s' members=%d",
            ch.id,
            base,
            game or "None",
            target,
            members_count,
        )
    _rename_state[ch.id] = (ch.name, members_count, game)


async def _set_speak_permission(
    channel: discord.VoiceChannel,
    member: discord.Member,
    allow: bool,
    reason: str,
) -> bool:
    """Allow or deny the ability to speak for a member in a channel.

    Opère uniquement sur le salon radio dédié (RADIO_VC_ID).
    """
    if channel.id != RADIO_VC_ID:
        return False
    try:
        if allow:
            await channel.set_permissions(member, overwrite=None, reason=reason)
            logging.info(
                f"[radio] Parole rétablie pour {member} dans {channel} — {reason}"
            )
        else:
            overwrite = channel.overwrites_for(member)
            overwrite.speak = False
            await channel.set_permissions(member, overwrite=overwrite, reason=reason)
            logging.info(
                f"[radio] Parole retirée pour {member} dans {channel} — {reason}"
            )
        return True
    except discord.Forbidden:
        logging.error(
            f"[radio] Forbidden: impossible de modifier la parole de {member} dans {channel}."
        )
    except Exception as e:
        logging.error(
            f"[radio] Erreur lors de la modification de la parole de {member} dans {channel}: {e}"
        )
    return False


# ─ INSERT HERE ─ [HELPERS MUSIQUE]

_YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "extract_flat": False,
    "no_warnings": True,
    "geo_bypass": True,
    "cachedir": False,
}

# FFmpeg: options robustes pour stream (reconnexion automatique, buffer raisonnable)
_FF_BEFORE = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"
)
_FF_OPTS = "-vn -loglevel error"


async def _ytdlp_get_best_audio_url(youtube_url: str) -> tuple[str, str]:
    """
    Retourne (stream_url, headers_str) ; headers_str est au format 'Key: Val\\r\\n...' pour FFmpeg -headers.
    """
    loop = asyncio.get_running_loop()

    def _extract():
        opts = dict(_YTDL_OPTS)
        # Client Android = liens HLS/DASH plus stables pour FFmpeg
        opts.update(
            {
                "extractor_args": {"youtube": {"player_client": ["android"]}},
            }
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)

            # URL du stream
            if "url" in info:
                stream_url = info["url"]
            elif "entries" in info and info["entries"]:
                ie = info["entries"][0]
                stream_url = (
                    ie.get("url") or ie.get("webpage_url") or youtube_url
                )
            else:
                stream_url = info.get("webpage_url") or youtube_url

            # En-têtes HTTP fournis par l’extractor (si présents)
            hdrs = info.get("http_headers") or {}

            # Toujours un User-Agent plausible (au cas où)
            hdrs.setdefault(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )

            # FFmpeg attend des lignes séparées par \r\n
            header_str = "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
            return stream_url, header_str

    return await loop.run_in_executor(None, _extract)


async def _reset_voice_session(guild: discord.Guild):
    """Déconnecte proprement le voice client pour repartir d'une session saine."""
    vc = guild.voice_client
    if vc:
        try:
            await vc.disconnect(force=True)
            logging.info(
                "[radio] Reset voice: disconnect(force=True) appliqué."
            )
        except Exception as e:
            logging.warning(
                f"[radio] Reset voice: échec disconnect forcé: {e}"
            )
    await asyncio.sleep(
        1.5
    )  # petit délai pour laisser Discord clôturer la session


async def _connect_voice(guild: discord.Guild) -> discord.VoiceClient | None:
    ch = guild.get_channel(RADIO_VC_ID)
    if not isinstance(ch, discord.VoiceChannel):
        logging.error(f"[radio] Salon vocal introuvable: {RADIO_VC_ID}")
        return None
    try:
        me = guild.me or guild.get_member(bot.user.id)

        # ✅ Permissions de base
        perms = ch.permissions_for(me) if me else None
        if not (perms and perms.connect and perms.speak):
            logging.error(
                "[radio] Permissions manquantes sur le salon: CONNECT/SPEAK requis."
            )
            return None

        # 🔒 Déjà connecté ailleurs → move
        if ch.guild.voice_client and ch.guild.voice_client.channel != ch:
            await ch.guild.voice_client.move_to(ch, reason="Radio auto")
            vc = ch.guild.voice_client
        elif ch.guild.voice_client:
            vc = ch.guild.voice_client
        else:
            # ❗ Toujours sans self_deaf
            vc = await ch.connect(
                reconnect=False, self_deaf=False, self_mute=False
            )

        # Enlever mute/deaf serveur si appliqués
        try:
            if me and me.voice and (me.voice.mute or me.voice.deaf):
                await me.edit(
                    mute=False, deafen=False, reason="Radio: ensure unmuted"
                )
                logging.info(
                    "[radio] Le bot était mute/deaf → correction appliquée."
                )
        except Exception as e:
            logging.warning(
                f"[radio] Impossible de forcer unmute/undeafen du bot: {e}"
            )

        # StageChannel → passer Speaker
        try:
            if (
                isinstance(ch, discord.StageChannel)
                and me
                and me.voice
                and me.voice.suppress
            ):
                await me.edit(
                    suppress=False, reason="Radio: passer en Speaker sur Stage"
                )
                logging.info(
                    "[radio] StageChannel: suppression levée (Speaker)."
                )
        except Exception as e:
            logging.warning(
                f"[radio] Stage unsuppress impossible (perms ?): {e}"
            )

        # 🧪 Log d'état audio
        st = me.voice
        logging.info(
            f"[radio] Bot voice state: ch={getattr(st.channel,'id',None)} "
            f"mute={getattr(st,'mute',None)} deaf={getattr(st,'deaf',None)} "
            f"self_mute={getattr(st,'self_mute',None)} self_deaf={getattr(st,'self_deaf',None)} "
            f"suppress={getattr(st,'suppress',None)}"
        )
        return vc

    except discord.errors.ConnectionClosed as e:
        code = getattr(e, "code", None)
        logging.warning(
            f"[radio] Voice ConnectionClosed (code={code}), tentative de reset…"
        )
        if code in (4006, 4009):
            await _reset_voice_session(guild)
            try:
                # ❗ garder self_deaf=False ici aussi
                vc = await ch.connect(
                    reconnect=False, self_deaf=False, self_mute=False
                )
                logging.info("[radio] Reconnexion voice réussie après reset.")
                return vc
            except Exception as ee:
                logging.error(f"[radio] Reconnexion après reset échouée: {ee}")
                return None
        else:
            logging.error(
                f"[radio] Voice WS fermé (code={code}) sans recovery dédié."
            )
            return None

    except Exception as e:
        logging.error(f"[radio] Connexion au vocal échouée: {e}")
        # Dernière chance: reset puis 2nd essai
        try:
            await _reset_voice_session(guild)
            # ❗ garder self_deaf=False ici aussi
            vc = await ch.connect(
                reconnect=False, self_deaf=False, self_mute=False
            )
            logging.info(
                "[radio] Connexion voice OK après reset (path générique)."
            )
            return vc
        except Exception as ee:
            logging.error(f"[radio] Connexion après reset échouée: {ee}")
            return None


def _ff_headers() -> str:
    return (
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
        "Icy-MetaData: 1\r\n"
        "Accept: */*\r\n"
    )


def _before_opts() -> str:
    return (
        "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
        "-protocol_whitelist file,http,https,tcp,tls,crypto,pipe "
        f'-headers "{_ff_headers()}"'
    )


def _wire_ffmpeg_stderr_to_log(source):
    proc = getattr(source, "_process", None) or getattr(
        source, "process", None
    )
    if not proc or not getattr(proc, "stderr", None):
        return
    import threading, collections

    tail = collections.deque(maxlen=50)

    def _reader():
        try:
            for line in iter(proc.stderr.readline, b""):
                try:
                    text = line.decode("utf-8", "ignore").rstrip()
                    tail.append(text)
                    logging.debug("[ffmpeg] " + text)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            proc._stderr_tail = list(tail)
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()


# ─play_once ─
async def _play_once(guild: discord.Guild) -> None:
    vc = await _connect_voice(guild)
    if not vc:
        await asyncio.sleep(5)
        return

    # Stop éventuel
    try:
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await asyncio.sleep(0.2)
    except Exception:
        pass

    # Vérif FFmpeg
    if not FFMPEG_PATH or not os.path.isfile(FFMPEG_PATH):
        logging.error(f"[radio] FFmpeg introuvable à : {FFMPEG_PATH}")
        await asyncio.sleep(5)
        return

    # ---------- Création de la source ----------

    bitrate = 96
    try:
        if getattr(guild, "premium_tier", 0) >= 1:
            bitrate = 128
    except Exception:
        pass

    try:
        source = discord.FFmpegOpusAudio(
            RADIO_STREAM_URL,
            bitrate=bitrate,
            before_options=_before_opts(),
            options="-vn -ar 48000 -ac 2 -vbr on",
        )
        logging.info(
            f"[radio] Source FFmpegOpusAudio prête ({bitrate} kbps)."
        )
    except Exception:
        logging.exception("[radio] Préparation source échouée")
        await asyncio.sleep(5)
        return

    # Brancher logs FFmpeg
    _wire_ffmpeg_stderr_to_log(source)

    done = asyncio.Event()

    def _after(err: Exception | None):
        rc = None
        proc = None
        try:
            proc = getattr(source, "_process", None) or getattr(
                source, "process", None
            )
            if proc:
                try:
                    proc.wait()
                except Exception:
                    pass
                rc = getattr(proc, "returncode", None)
        except Exception:
            proc = None
            pass

        if rc is not None and rc < 0:
            logging.error(f"[radio] FFmpeg exited with signal {-rc} (rc={rc})")
            tail = getattr(proc, "_stderr_tail", None)
            if tail:
                logging.error(
                    "[radio] FFmpeg stderr tail:\n" + "\n".join(tail)
                )
            try:
                bot.loop.call_soon_threadsafe(
                    bot.loop.create_task, _play_once(guild)
                )
            except Exception:
                logging.exception(
                    "[radio] Failed to schedule retry after crash"
                )
        elif err:
            logging.warning(
                f"[radio] Lecture terminée avec erreur: {err} (rc={rc})"
            )
        else:
            logging.info(f"[radio] Lecture terminée (rc={rc})")
        try:
            done.set()
        except Exception:
            pass

    # Lancer la lecture
    try:
        vc.play(source, after=_after)
        logging.info("[radio] ▶️ Lecture démarrée.")
    except Exception:
        logging.exception("[radio] Impossible de lancer la lecture")
        try:
            source.cleanup()
        except Exception:
            pass
        await asyncio.sleep(5)
        return

    # Surveillance
    try:
        while not done.is_set():
            if not vc.is_connected():
                logging.warning("[radio] VC déconnecté — relance prévue.")
                break
            if not vc.is_playing():
                await asyncio.sleep(2)
                if not vc.is_playing():
                    logging.warning("[radio] Flux stoppé — relance prévue.")
                    break
            await asyncio.sleep(3)
    finally:
        try:
            vc.stop()
        except Exception:
            pass
        try:
            source.cleanup()
        except Exception:
            pass


# ───────────────── RADIO: boucle principale ─────────────────
async def _radio_loop():
    """Assure la lecture H24: (re)connecte et relance si besoin."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for guild in bot.guilds:
                async with _radio_lock:
                    await _play_once(guild)
        except Exception as e:
            logging.error(f"[radio] Exception non gérée dans la boucle: {e}")
        # petite pause pour éviter les boucles agressives en cas d'échec
        await asyncio.sleep(2)


# ── RÉCOMPENSES NIVEAU ─────────────────────────────────────
async def grant_level_roles(
    member: discord.Member, new_level: int
) -> int | None:
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
            await member.add_roles(
                role, reason=f"Palier atteint: niveau {new_level}"
            )
    except Exception as e:
        logging.error(
            f"Impossible d'ajouter le rôle {role_id} à {member}: {e}"
        )
        return None

    # Optionnel: retirer les anciens paliers
    if REMOVE_LOWER_TIER_ROLES:
        try:
            lower_roles = [
                member.guild.get_role(LEVEL_ROLE_REWARDS[l])
                for l in LEVEL_ROLE_REWARDS.keys()
                if l < best_lvl
            ]
            lower_roles = [r for r in lower_roles if r and r in member.roles]
            if lower_roles:
                await member.remove_roles(
                    *lower_roles, reason="Nouveau palier atteint"
                )
        except Exception as e:
            logging.error(
                f"Impossible de retirer les anciens rôles à {member}: {e}"
            )

    return role_id


# ── ANNONCE LEVEL-UP (NOUVEAU STYLE, SANS IMAGE) ───────────
async def announce_level_up(
    guild: discord.Guild,
    member: discord.Member,
    old_level: int,
    new_level: int,
    xp: int,
):
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
        timestamp=datetime.now(PARIS_TZ),
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


def incr_daily_stat(
    guild_id: int, user_id: int, *, msg_inc: int = 0, voice_min_inc: int = 0
):
    stats = load_daily_stats()
    g = str(guild_id)
    date_key = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
    stats.setdefault(g, {}).setdefault(date_key, {}).setdefault(
        str(user_id), {"msg": 0, "voice_min": 0}
    )
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


# ─────────────────────── COMMANDES SLASH ──────────────────
@bot.tree.command(
    name="type_joueur", description="Choisir PC, Console ou Mobile"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def type_joueur(interaction: discord.Interaction):
    await safe_respond(
        interaction,
        f"Les boutons ont été postés dans <#{CHANNEL_ROLES}> 😉",
        ephemeral=True,
    )
    channel = interaction.guild.get_channel(CHANNEL_ROLES)
    if channel:
        await channel.send(
            "Quel type de joueur es-tu ?", view=PlayerTypeView()
        )


@bot.tree.command(name="sondage", description="Créer un sondage Oui/Non")
@app_commands.describe(question="La question à poser")
async def sondage(interaction: discord.Interaction, question: str):
    msg = await interaction.channel.send(
        f"📊 **{question}**\n> ✅ = Oui   ❌ = Non\n_Posé par {interaction.user.mention}_"
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await safe_respond(interaction, "Sondage créé ✔️", ephemeral=True)


@bot.tree.command(
    name="lien",
    description="Affiche le lien pour rejoindre le serveur Discord",
)
async def lien(interaction: discord.Interaction):
    await safe_respond(
        interaction,
        "🔗 Voici le lien pour rejoindre notre serveur :\nhttps://discord.com/invite/lerefuge57",
        ephemeral=False,
    )


# 🧪 MESSAGE D'ESSAI DANS LE SALON NIVEAUX (owner-only)
@bot.tree.command(
    name="test_niveau",
    description="Tester l'annonce de level-up (réservé au propriétaire)",
)
@app_commands.describe(
    niveau="(Optionnel) Test simple: nouveau niveau à simuler",
    membre="(Optionnel) Membre ciblé (par défaut: toi)",
    ancien_niveau="(Optionnel) Ancien niveau (défaut 4)",
    nouveau_niveau="(Optionnel) Nouveau niveau (défaut 5)",
    xp="(Optionnel) XP actuel; par défaut = seuil du nouveau niveau",
)
async def test_niveau(
    interaction: discord.Interaction,
    niveau: int | None = None,
    membre: discord.Member | None = None,
    ancien_niveau: app_commands.Range[int, 0, 999] = 4,
    nouveau_niveau: app_commands.Range[int, 1, 1000] = 5,
    xp: app_commands.Range[int, 0, 10_000_000] | None = None,
):
    # Restriction propriétaire (utiliser OWNER_ID)
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "❌ Commande réservée au propriétaire.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Mode simple: /test_niveau niveau:12
    if niveau is not None:
        if niveau <= 0:
            await interaction.followup.send(
                "❌ Le niveau doit être > 0.", ephemeral=True
            )
            return
        member = interaction.user
        old_lvl = max(0, niveau - 1)
        new_lvl = niveau
        xp_val = xp if xp is not None else (new_lvl**2 * 100)
    else:
        # Mode avancé: membre/anciens/nouveaux/xp
        member = membre or interaction.user
        if nouveau_niveau <= ancien_niveau:
            await interaction.followup.send(
                "❌ Le nouveau niveau doit être supérieur à l'ancien.",
                ephemeral=True,
            )
            return
        old_lvl = ancien_niveau
        new_lvl = nouveau_niveau
        xp_val = xp if xp is not None else (new_lvl**2 * 100)

    try:
        await announce_level_up(
            interaction.guild, member, old_lvl, new_lvl, xp_val
        )
        await interaction.followup.send(
            "✅ Message d'essai envoyé dans le salon niveaux.", ephemeral=True
        )
    except Exception as e:
        logging.error(f"/test_niveau échec: {e}")
        await interaction.followup.send(
            "❌ Impossible d'envoyer le message d'essai.", ephemeral=True
        )


@bot.tree.command(
    name="rang", description="Affiche ton niveau avec une carte graphique"
)
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
            await interaction.followup.send(
                "Tu n'as pas encore de niveau... Commence à discuter !",
                ephemeral=True,
            )
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
            logging.warning(
                f"/rang: envoi ephemeral échoué, fallback public. Raison: {e}"
            )
            await interaction.channel.send(
                content=f"{interaction.user.mention} voici ta carte de niveau :",
                file=file,
            )
            await interaction.followup.send(
                "Je n'ai pas pu l'envoyer en privé, je l'ai postée dans le salon.",
                ephemeral=True,
            )
    except ImportError as e:
        logging.exception(f"/rang: ImportError (Pillow manquante ?) {e}")
        await interaction.followup.send(
            "❌ Erreur interne: dépendance manquante (Pillow).", ephemeral=True
        )
    except Exception as e:
        logging.exception(f"/rang: exception inattendue: {e}")
        await interaction.followup.send(
            "❌ Une erreur est survenue pendant la génération de la carte.",
            ephemeral=True,
        )


@bot.tree.command(
    name="xp_serveur",
    description="Affiche l'XP de tous les membres du serveur",
)
@app_commands.check(is_xp_viewer)
async def xp_serveur(interaction: discord.Interaction):
    async with XP_LOCK:
        items = list(XP_CACHE.items())
    if not items:
        await safe_respond(interaction, "Aucune donnée XP.", ephemeral=True)
        return
    lines = []
    for uid, data in sorted(items, key=lambda x: x[1].get("xp", 0), reverse=True):
        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
        if not member:
            continue
        xp = int(data.get("xp", 0))
        lvl = int(data.get("level", 0))
        lines.append(f"{member.display_name} - {xp} XP (niveau {lvl})")
    if not lines:
        await safe_respond(interaction, "Aucun membre trouvé.", ephemeral=True)
        return
    report = "\n".join(lines)
    if len(report) < 1900:
        await safe_respond(interaction, f"```\n{report}\n```", ephemeral=True)
    else:
        file = discord.File(io.StringIO(report), filename="xp_serveur.txt")
        await safe_respond(
            interaction, "📄 Liste XP en pièce jointe.", ephemeral=True, file=file
        )


@bot.tree.command(
    name="vocaux",
    description="Publier (ou ré-attacher) les boutons pour créer des salons vocaux",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def vocaux(interaction: discord.Interaction):
    await safe_respond(
        interaction,
        "⏳ Je (ré)publie les boutons dans le salon lobby…",
        ephemeral=True,
    )
    await ensure_vc_buttons_message()
    await interaction.followup.send(
        "📌 Boutons OK dans le salon prévu.", ephemeral=True
    )


@bot.tree.command(
    name="purge",
    description="Supprime N messages récents de ce salon (réservé à Kevin)",
)
@app_commands.describe(nb="Nombre de messages à supprimer (1-100)")
async def purge(
    interaction: discord.Interaction, nb: app_commands.Range[int, 1, 100]
):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
    except Exception:
        pass

    if interaction.user.id != OWNER_ID:
        await interaction.followup.send(
            "❌ Commande réservée au propriétaire.", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.followup.send(
            "❌ Utilisable uniquement sur un serveur.", ephemeral=True
        )
        return
    ch = interaction.channel
    if ch is None:
        await interaction.followup.send(
            "❌ Salon introuvable.", ephemeral=True
        )
        return

    me = interaction.guild.me or interaction.guild.get_member(bot.user.id)
    if not me:
        await interaction.followup.send(
            "❌ Impossible de vérifier mes permissions.", ephemeral=True
        )
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
            deleted = await ch.purge(
                limit=nb, check=lambda m: not m.pinned, bulk=True
            )
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


# ─────────────────────── TÂCHES DE FOND ────────────────────
async def ensure_vc_buttons_message():
    await bot.wait_until_ready()
    channel = bot.get_channel(LOBBY_TEXT_CHANNEL)
    if not isinstance(channel, discord.TextChannel):
        logging.warning(f"❌ Salon lobby introuvable: {LOBBY_TEXT_CHANNEL}")
        return

    view = VCButtonView()

    # Texte visible par les membres (sans le marqueur)
    display_text = (
        "🎮 Rejoins d'abord <#1405630965803520221> puis choisis ton salon :\n"
        "• 💻 PC\n"
        "• 🔄 Crossplay\n"
        "• 🎮 Consoles\n"
        "• 💬 Chat"
    )
    embed = discord.Embed(description=display_text, color=0x00C896)

    # 1) Essayer avec l'ID mémorisé
    remembered_id = _load_perma_msg_id()
    if remembered_id:
        try:
            msg = await channel.fetch_message(remembered_id)
            await msg.edit(content=None, embeds=[embed], view=view)
            try:
                await msg.pin(reason="Message permanent des salons vocaux")
            except Exception:
                pass
            logging.info("🔁 Message permanent (ID mémorisé) réattaché.")
            return
        except Exception:
            logging.debug(
                "Message mémorisé introuvable — on va rechercher/reposter."
            )

    # 2) Chercher un message existant marqué (footer OU contenu legacy)
    found = None
    try:
        async for m in channel.history(limit=100):
            if _is_vc_permanent_message(m):
                found = m
                break
    except Exception as e:
        logging.error(f"Erreur lecture historique: {e}")

    if found:
        try:
            await found.edit(content=None, embeds=[embed], view=view)
            try:
                await found.pin(reason="Message permanent des salons vocaux")
            except Exception:
                pass
            _save_perma_msg_id(found.id)
            logging.info("🔁 Message permanent réattaché (via recherche).")
            return
        except Exception as e:
            logging.error(f"Échec réattachement, on reposte: {e}")

    # 3) Reposter un nouveau message (sans marqueur en clair)
    try:
        new_msg = await channel.send(embed=embed, view=view)
        _save_perma_msg_id(new_msg.id)
        try:
            await new_msg.pin(reason="Message permanent des salons vocaux")
        except Exception:
            pass
        logging.info("📌 Message permanent publié (nouveau).")
    except Exception as e:
        logging.error(f"Erreur envoi message permanent: {e}")


async def update_daily_roles(
    guild: discord.Guild, top_msg, top_vc, top_mvp
) -> None:
    """Met à jour les rôles quotidiens pour les gagnants."""
    role_map = [
        (TOP_MSG_ROLE_ID, top_msg),
        (TOP_VC_ROLE_ID, top_vc),
        (MVP_ROLE_ID, top_mvp),
    ]

    for role_id, ranking in role_map:
        role = guild.get_role(role_id)
        if not role or not ranking:
            continue
        winner_id = int(ranking[0][0])
        winner = guild.get_member(winner_id)
        if not winner:
            continue

        # Suppression du rôle pour les précédents détenteurs
        for member in list(role.members):
            try:
                await member.remove_roles(role, reason="Daily role reset")
            except discord.Forbidden:
                # Gestion d'erreur: permissions manquantes
                logging.warning(
                    f"Permission manquante pour retirer {role.id} à {member.id}"
                )

        # Attribution du rôle au gagnant
        try:
            await winner.add_roles(
                role, reason="Top message/vocal/MVP de la journée"
            )
        except discord.Forbidden:
            # Gestion d'erreur: impossible d'attribuer
            logging.error(
                f"Permission manquante pour attribuer {role.id} à {winner.id}"
            )


async def daily_summary_loop():
    """
    Chaque minuit (Europe/Paris), poste le résumé d’activité du jour écoulé pour CHAQUE serveur.
    """
    await bot.wait_until_ready()
    while not bot.is_closed():
        # dormir jusqu'au prochain minuit (heure locale Paris)
        now = datetime.now(PARIS_TZ)
        tomorrow = now + timedelta(days=1)
        next_midnight = tomorrow.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
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

            top_msg = sorted(items, key=lambda x: x[1], reverse=True)[:5]
            top_vc = sorted(items, key=lambda x: x[2], reverse=True)[:5]
            top_mvp = sorted(items, key=lambda x: x[3], reverse=True)[:5]

            total_msgs = sum(x[1] for x in items)
            total_vmin = sum(x[2] for x in items)

            embed = discord.Embed(
                title=f"📈 Résumé du jour — {day_key}",
                description=f"**Total** : {total_msgs} messages • {total_vmin} min en vocal",
                color=0x00C896,
            )
            if top_msg:
                embed.add_field(
                    name="💬 Top Messages",
                    value="\n".join(
                        [
                            f"**{i+1}.** {u_name(uid)} — {msgs} msgs"
                            for i, (uid, msgs, _, _) in enumerate(top_msg)
                        ]
                    ),
                    inline=False,
                )
            if top_vc:
                embed.add_field(
                    name="🎙️ Top Vocal (min)",
                    value="\n".join(
                        [
                            f"**{i+1}.** {u_name(uid)} — {vmin} min"
                            for i, (uid, _, vmin, _) in enumerate(top_vc)
                        ]
                    ),
                    inline=False,
                )
            if top_mvp:
                embed.add_field(
                    name="🏆 MVP (messages + minutes)",
                    value="\n".join(
                        [
                            f"**{i+1}.** {u_name(uid)} — {score} pts"
                            for i, (uid, msgs, vmin, score) in enumerate(
                                top_mvp
                            )
                        ]
                    ),
                    inline=False,
                )

            ch = guild.get_channel(ACTIVITY_SUMMARY_CH)
            if isinstance(ch, discord.TextChannel):
                try:
                    me = guild.me or guild.get_member(bot.user.id)
                    if not ch.permissions_for(me).mention_everyone:
                        await ch.send(
                            "⚠️ Je n'ai pas la permission de mentionner @everyone ici."
                        )
                        content = "Voici les joueurs les plus actifs d'hier !"
                        await ch.send(content=content, embed=embed)
                    else:
                        await ch.send(
                            content="@everyone — Voici les joueurs les plus actifs d'hier !",
                            embed=embed,
                            allowed_mentions=discord.AllowedMentions(
                                everyone=True
                            ),
                        )
                    if top_msg and top_vc and top_mvp:
                        await update_daily_roles(guild, top_msg, top_vc, top_mvp)
                except Exception as e:
                    logging.error(
                        f"Envoi résumé quotidien échoué (guild {guild.id}): {e}"
                    )

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
        next_run = (now + timedelta(days=days_ahead)).replace(
            hour=0, minute=5, second=0, microsecond=0
        )
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))

        # après le réveil : calcul de la semaine précédente (lundi→dimanche)
        today = datetime.now(PARIS_TZ).date()  # on est lundi (date du "run")
        last_sunday = today - timedelta(
            days=today.weekday() + 1
        )  # hier (dimanche)
        last_monday = last_sunday - timedelta(days=6)  # lundi précédent

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

            table = [
                (uid, v["msg"], v["voice_min"], v["msg"] + v["voice_min"])
                for uid, v in items.items()
            ]
            top_msg = sorted(table, key=lambda x: x[1], reverse=True)[:5]
            top_vc = sorted(table, key=lambda x: x[2], reverse=True)[:5]
            top_mvp = sorted(table, key=lambda x: x[3], reverse=True)[:5]

            total_msgs = sum(x[1] for x in table)
            total_vmin = sum(x[2] for x in table)

            period_text = f"{last_monday.strftime('%Y-%m-%d')} → {last_sunday.strftime('%Y-%m-%d')}"
            embed = discord.Embed(
                title=f"🏁 Résumé hebdo — {period_text}",
                description=f"**Totaux** : {total_msgs} messages • {total_vmin} min en vocal",
                color=0x5865F2,
            )

            def medal(i):
                return (
                    "🥇"
                    if i == 0
                    else ("🥈" if i == 1 else ("🥉" if i == 2 else f"{i+1}."))
                )

            if top_msg:
                embed.add_field(
                    name="💬 Top Messages",
                    value="\n".join(
                        [
                            f"**{medal(i)}** {u_name(uid)} — {msgs} msgs"
                            for i, (uid, msgs, _, _) in enumerate(top_msg)
                        ]
                    ),
                    inline=False,
                )
            if top_vc:
                embed.add_field(
                    name="🎙️ Top Vocal",
                    value="\n".join(
                        [
                            f"**{medal(i)}** {u_name(uid)} — {vmin} min"
                            for i, (uid, _, vmin, _) in enumerate(top_vc)
                        ]
                    ),
                    inline=False,
                )
            if top_mvp:
                embed.add_field(
                    name="🏆 MVP (msgs + min)",
                    value="\n".join(
                        [
                            f"**{medal(i)}** {u_name(uid)} — {score} pts"
                            for i, (uid, _, __, score) in enumerate(top_mvp)
                        ]
                    ),
                    inline=False,
                )

            ch = guild.get_channel(ACTIVITY_SUMMARY_CH)
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.send(
                        content="@everyone — Podium de la semaine !",
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(
                            everyone=True
                        ),
                    )
                except Exception as e:
                    logging.error(
                        f"Envoi résumé hebdo échoué (guild {guild.id}): {e}"
                    )


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
        await asyncio.sleep(5)  # toutes les 5s pour une réaction plus rapide


async def vc_buttons_watchdog(interval_seconds: int = 120):
    """Vérifie périodiquement que le message permanent existe et est à jour."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await ensure_vc_buttons_message()
        except Exception as e:
            logging.debug(f"vc_buttons_watchdog: {e}")
        await asyncio.sleep(interval_seconds)


# ─────────────────────── VIEWS ─────────────────────────────
class LiveTikTokView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔴 Annoncer le live TikTok",
        style=discord.ButtonStyle.danger,
        custom_id="announce_live",
    )
    async def announce_live(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        channel = bot.get_channel(TIKTOK_ANNOUNCE_CH)
        if not channel:
            await safe_respond(
                interaction, "❌ Salon cible introuvable.", ephemeral=True
            )
            return
        guild = interaction.guild
        me = guild.me or guild.get_member(bot.user.id)
        if not channel.permissions_for(me).mention_everyone:
            await safe_respond(
                interaction,
                "❌ Je n'ai pas la permission de mentionner @everyone dans ce salon.",
                ephemeral=True,
            )
            return
        await channel.send(
            "@everyone 🚨 Kevin est en LIVE sur TikTok !\n🔴 Rejoins maintenant : https://www.tiktok.com/@kevinlerefuge",
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        await safe_respond(interaction, "✅ Annonce envoyée !", ephemeral=True)


class VCButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def create_vc(self, interaction: discord.Interaction, profile: str):
        guild = interaction.guild
        if guild is None:
            return await safe_respond(
                interaction, "❌ Action impossible en DM.", ephemeral=True
            )

        member = interaction.user

        # Vérif présence dans le lobby
        if not member.voice or not member.voice.channel:
            return await safe_respond(
                interaction,
                "⛔ Rejoins d’abord le **vocal lobby** puis reclique sur un bouton.",
                ephemeral=True,
            )
        if member.voice.channel.id != LOBBY_VC_ID:
            return await safe_respond(
                interaction,
                "⛔ Tu dois être **dans le vocal lobby** pour choisir le type (PC/Consoles/Crossplay/Chat).",
                ephemeral=True,
            )

        # Vérif permissions du bot
        me = guild.me or guild.get_member(bot.user.id)
        if not (me and me.guild_permissions.move_members):
            return await safe_respond(
                interaction,
                "⛔ Il me manque la permission **Déplacer des membres**.",
                ephemeral=True,
            )

        # Vérif catégorie
        category = guild.get_channel(TEMP_VC_CATEGORY)
        if not isinstance(category, discord.CategoryChannel):
            return await safe_respond(
                interaction, "❌ Catégorie vocale temporaire introuvable.", ephemeral=True
            )

        perms_cat = category.permissions_for(me)
        if not (
            perms_cat.manage_channels
            and perms_cat.view_channel
            and perms_cat.connect
        ):
            return await safe_respond(
                interaction,
                "⛔ Permissions manquantes sur la catégorie (**Gérer les salons / Voir / Se connecter**).",
                ephemeral=True,
            )

        # Limite éventuelle
        limit = TEMP_VC_LIMITS.get(category.id)
        if limit is not None:
            current = sum(
                1 for ch in category.voice_channels if ch.id in TEMP_VC_IDS
            )
            if current >= limit:
                return await safe_respond(
                    interaction,
                    f"⛔ Limite atteinte : **{current}/{limit}** salons temporaires dans **{category.name}**.",
                    ephemeral=True,
                )

        # Création du vocal à partir du template + permission membre
        name = next_vc_name(guild, profile)
        template = discord.utils.get(
            category.voice_channels, name=TEMP_VC_TEMPLATE_NAME
        )
        if template is None:
            return await safe_respond(
                interaction, "❌ Salon template introuvable.", ephemeral=True
            )
        try:
            vc = await template.clone(
                name=name,
                reason=f"Salon temporaire ({profile}) demandé depuis le lobby par {member}",
            )
            if vc.category != category:
                await vc.edit(category=category)
            await vc.set_permissions(member, connect=True, speak=True)
            TEMP_VC_IDS.add(vc.id)
            save_temp_vc_ids(TEMP_VC_IDS)
        except Exception as e:
            logging.error(f"Erreur création VC: {e}")
            return await safe_respond(
                interaction, "❌ Impossible de créer le salon.", ephemeral=True
            )

        # Déplacement obligatoire
        try:
            await member.move_to(
                vc, reason="Choix de type depuis le lobby (move obligatoire)"
            )
        except Exception as e:
            logging.error(f"Move failed (rollback): {e}")
            try:
                await vc.delete(reason="Rollback: déplacement impossible")
            except Exception as de:
                logging.error(f"Rollback delete failed: {de}")
            TEMP_VC_IDS.discard(vc.id)
            save_temp_vc_ids(TEMP_VC_IDS)
            return await safe_respond(
                interaction,
                "❌ Je n’ai pas pu te déplacer. Vérifie que tu es bien **dans le vocal lobby** et réessaie.",
                ephemeral=True,
            )

        # Auto-rename initial
        try:
            await maybe_rename_channel_by_game(vc, wait_presences=True)
        except Exception:
            pass

        # Confirmation
        await safe_respond(
            interaction, f"🚀 Tu as été déplacé dans **{vc.name}**.", ephemeral=True
        )

    # Boutons
    @discord.ui.button(
        label="💻 PC",
        style=discord.ButtonStyle.primary,
        custom_id="create_vc_pc",
    )
    async def btn_pc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.create_vc(interaction, "PC")

    @discord.ui.button(
        label="🎮 Consoles",
        style=discord.ButtonStyle.primary,
        custom_id="create_vc_consoles",
    )
    async def btn_consoles(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.create_vc(interaction, "Consoles")

    @discord.ui.button(
        label="🔀 Crossplay",
        style=discord.ButtonStyle.primary,
        custom_id="create_vc_crossplay",
    )
    async def btn_crossplay(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.create_vc(interaction, "Crossplay")

    @discord.ui.button(
        label="💬 Chat",
        style=discord.ButtonStyle.secondary,
        custom_id="create_vc_chat",
    )
    async def btn_chat(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.create_vc(interaction, "Chat")


# ─────────────────────── EVENTS ─────────────────────────────
@bot.tree.command(
    name="roles_refresh",
    description="Reposter/reattacher le message des rôles (réservé au propriétaire)",
)
async def roles_refresh(interaction: discord.Interaction):
    if interaction.user.id != 541417878314942495:
        return await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.",
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True)
    await ensure_roles_buttons_message()
    await interaction.followup.send(
        "✅ Message de rôles (re)publié/reattaché.", ephemeral=True
    )


@bot.event
async def on_ready():
    # chunk des guilds
    try:
        for g in bot.guilds:
            await g.chunk()
    except Exception as e:
        logging.debug(f"chunk failed: {e}")

    # présence
    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.playing, name=".gg/lerefuge57"
            ),
        )
    except Exception as e:
        logging.debug(f"presence failed: {e}")

    await ensure_channel_has_message(
        bot,
        ROULETTE_CHANNEL_ID,
        "Premier message dans ce salon !",
    )

    await ensure_temp_vc_template(bot)
    await delete_untracked_temp_vcs(bot, TEMP_VC_CATEGORY, TEMP_VC_IDS)
    await _rebuild_temp_vc_ids()

    # ─ DÉMARRAGE RADIO ─
    global _radio_task
    if _radio_task is None or _radio_task.done():
        _radio_task = asyncio.create_task(_radio_loop())
        logging.info("[radio] Boucle radio initialisée.")

    for g in bot.guilds:
        await _refresh_stats_channels(g)

    if not _stats_update_loop.is_running():
        _stats_update_loop.start()

    logging.info(
        f"✅ Connecté en tant que {bot.user} (latence {bot.latency*1000:.0f} ms)"
    )


async def _ensure_stats_channels(guild: discord.Guild) -> dict[str, discord.VoiceChannel]:
    """Crée les salons de statistiques si nécessaire et les renvoie."""
    category = guild.get_channel(STATS_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        logging.warning(
            f"Catégorie statistiques introuvable ({STATS_CATEGORY_ID}) pour {guild.name}."
        )
        return {}

    channels: dict[str, discord.VoiceChannel] = {}
    for key, base_name in STATS_CHANNELS.items():
        channel = next(
            (c for c in category.voice_channels if c.name.startswith(base_name)),
            None,
        )
        if channel is None:
            channel = await guild.create_voice_channel(base_name, category=category)
            try:
                overwrites = channel.overwrites_for(guild.default_role)
                overwrites.connect = False
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrites
                )
            except Exception as e:
                logging.warning(
                    f"Impossible de configurer les permissions du salon stats: {e}"
                )
        channels[key] = channel
    return channels


async def _refresh_stats_channels(guild: discord.Guild) -> None:
    """Met à jour les noms des salons statistiques."""
    channels = await _ensure_stats_channels(guild)
    if not channels:
        return

    total_members = guild.member_count
    online_members = sum(
        1 for m in guild.members if m.status != discord.Status.offline
    )
    voice_members = sum(1 for m in guild.members if m.voice)

    try:
        await channels["members"].edit(
            name=f"{STATS_CHANNELS['members']} : {total_members}"
        )
        await channels["online"].edit(
            name=f"{STATS_CHANNELS['online']} : {online_members}"
        )
        await channels["voice"].edit(
            name=f"{STATS_CHANNELS['voice']} : {voice_members}"
        )
    except Exception as e:
        logging.warning(f"Impossible de mettre à jour les salons de stats: {e}")


@tasks.loop(minutes=10)
async def _stats_update_loop() -> None:
    for guild in bot.guilds:
        await _refresh_stats_channels(guild)


@bot.event
async def on_member_join(member: discord.Member):
    await _refresh_stats_channels(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):
    await _refresh_stats_channels(member.guild)


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
            await announce_level_up(
                message.guild,
                message.author,
                old_level,
                new_level,
                int(user["xp"]),
            )
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
        description=(
            f"{member.mention}, installe-toi bien !\n🕹️ Choisis ton rôle dans <#{CHANNEL_ROLES}> pour accéder à toutes les sections.\nRavi de t’avoir parmi nous 🎮"
        ),
        color=0x00FFCC,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Membre #{len(member.guild.members)}")
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logging.error(f"Erreur envoi bienvenue : {e}")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    uid = str(member.id)

    # ── Auto-rename: ancien et nouveau salon (no-op si AUTO_RENAME_ENABLED=False)
    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        await maybe_rename_channel_by_game(before.channel, wait_presences=True)
    if after.channel and isinstance(after.channel, discord.VoiceChannel):
        await maybe_rename_channel_by_game(after.channel, wait_presences=True)

    # ⛔ Ignore entièrement les utilisateurs bot pour l'XP vocal / chrono / stats
    if member.bot:
        voice_times.pop(uid, None)  # nettoie un éventuel chrono
        return

    # ── Chrono XP (UTC aware)
    now_utc = datetime.now(timezone.utc)

    # Connexion au vocal → start chrono
    if after.channel and not before.channel:
        voice_times[uid] = now_utc

    # Déconnexion du vocal → calcule minutes + XP
    elif before.channel and not after.channel:
        started = voice_times.pop(uid, None)
        if started:
            seconds_spent = (now_utc - started).total_seconds()
            minutes_spent = int(seconds_spent // 60)
            if minutes_spent >= 1:
                gained_xp = minutes_spent * VOICE_XP_PER_MIN
                async with XP_LOCK:
                    user = XP_CACHE.setdefault(uid, {"xp": 0, "level": 0})
                    user["xp"] += gained_xp
                    user["level"] = get_level(int(user["xp"]))
                incr_daily_stat(
                    member.guild.id, member.id, voice_min_inc=minutes_spent
                )
        

    # Move de salon → clôture partielle + restart chrono
    elif before.channel and after.channel and before.channel != after.channel:
        started = voice_times.get(uid)
        if started:
            seconds_spent = (now_utc - started).total_seconds()
            minutes_spent = int(seconds_spent // 60)
            if minutes_spent >= 1:
                gained_xp = minutes_spent * VOICE_XP_PER_MIN
                async with XP_LOCK:
                    user = XP_CACHE.setdefault(uid, {"xp": 0, "level": 0})
                    user["xp"] += gained_xp
                    user["level"] = get_level(int(user["xp"]))
                incr_daily_stat(
                    member.guild.id, member.id, voice_min_inc=minutes_spent
                )
        voice_times[uid] = now_utc

    # ── Suppression des salons temporaires vides (⚠️ jamais le salon radio)
    if (
        before.channel
        and before.channel.id in TEMP_VC_IDS
        and before.channel.id != RADIO_VC_ID
        and not before.channel.members
    ):
        try:
            await before.channel.delete(reason="Salon temporaire vide")
            TEMP_VC_IDS.discard(before.channel.id)
            save_temp_vc_ids(TEMP_VC_IDS)
        except Exception as e:
            logging.error(f"Suppression VC temporaire échouée: {e}")

    # ── Auto retrait/restitution de la parole sur le canal radio (uniquement humains)
    try:
        joined_radio = (
            after.channel
            and after.channel.id == RADIO_VC_ID
            and (not before.channel or before.channel.id != RADIO_VC_ID)
        )
        left_radio = (
            before.channel
            and before.channel.id == RADIO_VC_ID
            and (not after.channel or after.channel.id != RADIO_VC_ID)
        )

        if joined_radio and after.channel:
            await _set_speak_permission(
                after.channel,
                member,
                allow=False,
                reason=f"Auto retrait de la parole dans le canal radio {RADIO_VC_ID}",
            )
        elif left_radio and before.channel:
            await _set_speak_permission(
                before.channel,
                member,
                allow=True,
                reason=f"Auto restitution de la parole en sortant du canal radio {RADIO_VC_ID}",
            )
    except Exception as e:
        logging.error(f"[radio] Exception dans on_voice_state_update: {e}")

    # ── Auto mute/unmute pour un rôle spécifique dans le canal radio
    try:
        has_role = member.get_role(RADIO_MUTED_ROLE_ID) is not None
        joined_target = (
            has_role
            and after.channel
            and after.channel.id == RADIO_VC_ID
            and (not before.channel or before.channel.id != RADIO_VC_ID)
        )
        left_target = (
            has_role
            and before.channel
            and before.channel.id == RADIO_VC_ID
            and (not after.channel or after.channel.id != RADIO_VC_ID)
            and member.id in AUTO_MUTED_USERS
        )

        if joined_target:
            await member.edit(
                mute=True,
                reason=f"Auto mute rôle {RADIO_MUTED_ROLE_ID} dans le canal radio {RADIO_VC_ID}",
            )
            AUTO_MUTED_USERS.add(member.id)
        elif left_target:
            await member.edit(
                mute=False,
                reason=f"Auto unmute en quittant le canal radio {RADIO_VC_ID}",
            )
            AUTO_MUTED_USERS.discard(member.id)
    except Exception as e:
        logging.error(f"[radio-mute] Exception dans on_voice_state_update: {e}")


# ─────────────────────── SETUP ─────────────────────────────
async def _setup_hook():
    await xp_bootstrap_cache()
    voice_times.update(load_voice_times())

    # Exposer API pour les cogs
    bot.award_xp = award_xp
    bot.announce_level_up = announce_level_up

    bot.add_view(VCButtonView())
    bot.add_view(LiveTikTokView())
    bot.add_view(PlayerTypeView())

    asyncio.create_task(vc_buttons_watchdog())
    asyncio.create_task(auto_backup_xp())
    asyncio.create_task(ensure_vc_buttons_message())
    asyncio.create_task(ensure_roles_buttons_message())
    asyncio.create_task(daily_summary_loop())  # Résumé quotidien
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

    # Charger le module de rappels de rôles (72h / suppression 24h)
    try:
        await bot.load_extension("cogs.role_reminder")
        logging.info("⏰ Extension cogs.role_reminder chargée.")
    except Exception as e:
        logging.error(f"❌ Impossible de charger cogs.role_reminder: {e}")


async def ensure_roles_buttons_message():
    """
    (Re)poste le message permanent des rôles PC/Consoles/Mobile/Notifications
    dans CHANNEL_ROLES et (ré)attache la vue PlayerTypeView, sans afficher de marqueur dans le contenu.
    Reposte UNIQUEMENT s'il est manquant/supprimé.
    """
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ROLES)
    if not isinstance(channel, discord.TextChannel):
        logging.warning(f"❌ Salon des rôles introuvable: {CHANNEL_ROLES}")
        return

    view = PlayerTypeView()

    display_text = (
        "🎮 **Choisis ta plateforme** (exclusives) **et** active les notifications si tu veux être ping :\n"
        "• 💻 PC\n"
        "• 🎮 Consoles\n"
        "• 📱 Mobile\n"
        "• 🔔 Notifications *(ajout/retrait **indépendant**, conservé quand tu changes de plateforme)*"
    )
    embed = discord.Embed(description=display_text, color=0x00C896)

    # 1) D'abord: essayer par ID mémorisé
    remembered_id = _load_roles_perma_msg_id()
    if remembered_id:
        try:
            msg = await channel.fetch_message(remembered_id)
            await msg.edit(content=None, embeds=[embed], view=view)
            try:
                await msg.pin(reason="Message rôles permanent")
            except Exception:
                pass
            _save_roles_perma_msg_id(msg.id)
            logging.info(
                "🔁 [roles] Message permanent réattaché via ID mémorisé."
            )
            return
        except Exception:
            logging.debug(
                "[roles] ID mémorisé introuvable — on va rechercher dans l'historique."
            )

    # 2) Chercher dans l'historique un message existant (legacy ou composants)
    found = None
    try:
        async for m in channel.history(limit=100):
            if _is_roles_permanent_message(m):
                found = m
                break
    except Exception as e:
        logging.error(f"[roles] Erreur lecture historique: {e}")

    if found:
        try:
            await found.edit(content=None, embeds=[embed], view=view)
            try:
                await found.pin(reason="Message rôles permanent")
            except Exception:
                pass
            _save_roles_perma_msg_id(found.id)
            logging.info(
                "🔁 [roles] Message permanent réattaché (via recherche)."
            )
            return
        except Exception as e:
            logging.error(f"[roles] Échec réattachement, on reposte: {e}")

    # 3) Si rien trouvé: on (re)poste un nouveau message
    try:
        new_msg = await channel.send(embed=embed, view=view)
        _save_roles_perma_msg_id(new_msg.id)
        try:
            await new_msg.pin(reason="Message rôles permanent")
        except Exception:
            pass
        logging.info("📌 [roles] Message permanent publié (nouveau).")
    except Exception as e:
        logging.error(f"[roles] Erreur envoi message permanent: {e}")


bot.setup_hook = _setup_hook

# ─────────────────────── MAIN ──────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
