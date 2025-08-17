import asyncio
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.interactions import safe_respond

# Fichiers de persistance
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
XP_FILE = f"{DATA_DIR}/data.json"
BACKUP_FILE = f"{DATA_DIR}/backup.json"
VOICE_TIMES_FILE = f"{DATA_DIR}/voice_times.json"

# Caches en mémoire
voice_times: dict[str, datetime] = {}
XP_CACHE: dict[str, dict] = {}
XP_LOCK = asyncio.Lock()

def ensure_data_dir() -> None:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

def _safe_read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def save_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

def load_json(path: str) -> dict:
    return _safe_read_json(path)

def load_voice_times() -> dict[str, datetime]:
    data = load_json(VOICE_TIMES_FILE)
    out: dict[str, datetime] = {}
    for uid, iso in data.items():
        try:
            out[uid] = datetime.fromisoformat(iso)
        except Exception as e:
            logging.warning("Invalid voice time for user %s: %s", uid, e)
            continue
    return out

def save_voice_times(d: dict[str, datetime]) -> None:
    serializable = {uid: dt.astimezone(timezone.utc).isoformat() for uid, dt in d.items()}
    save_json(VOICE_TIMES_FILE, serializable)

def _disk_load_xp() -> dict:
    ensure_data_dir()
    path = Path(XP_FILE)
    backup_path = Path(BACKUP_FILE)
    try:
        if not path.exists():
            if backup_path.exists():
                data = _safe_read_json(BACKUP_FILE)
                save_json(XP_FILE, data)
                logging.info("📦 XP restauré depuis backup.json (fichier principal manquant).")
                return data
            save_json(XP_FILE, {})
            logging.info("📁 Fichier XP créé (vide).")
            return {}
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

def _disk_save_xp(data: dict) -> None:
    ensure_data_dir()
    save_json(XP_FILE, data)
    try:
        Path(BACKUP_FILE).write_text(Path(XP_FILE).read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as e:
        logging.error(f"❌ Écriture backup échouée: {e}")

async def xp_bootstrap_cache() -> None:
    global XP_CACHE, voice_times
    XP_CACHE = _disk_load_xp()
    voice_times = load_voice_times()
    logging.info("🎒 XP cache chargé (%d utilisateurs).", len(XP_CACHE))

async def xp_flush_cache_to_disk() -> None:
    async with XP_LOCK:
        _disk_save_xp(XP_CACHE)
        logging.info("💾 XP flush vers disque (%d utilisateurs).", len(XP_CACHE))

def get_level(xp: int) -> int:
    level = 0
    while xp >= (level + 1) ** 2 * 100:
        level += 1
    return level

async def award_xp(user_id: int, amount: int) -> tuple[int, int, int]:
    """Ajoute `amount` d'XP à `user_id` et retourne (old_level, new_level, total_xp)."""
    uid = str(user_id)
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

async def generate_rank_card(user: discord.User, level: int, xp: int, xp_needed: int):
    from PIL import Image, ImageDraw
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

class XPCog(commands.Cog):
    """Fonctionnalités liées à l'XP."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.auto_backup_xp.start()

    def cog_unload(self) -> None:
        self.auto_backup_xp.cancel()

    @tasks.loop(seconds=600)
    async def auto_backup_xp(self) -> None:
        await xp_flush_cache_to_disk()
        save_voice_times(voice_times)
        logging.info("🛟 Sauvegarde périodique effectuée.")

    @auto_backup_xp.before_loop
    async def before_auto_backup_xp(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="rang", description="Affiche ton niveau avec une carte graphique")
    async def rang(self, interaction: discord.Interaction) -> None:
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
            await interaction.followup.send(file=file, ephemeral=True)
        except Exception as e:
            logging.exception(f"/rang: exception inattendue: {e}")
            await interaction.followup.send(
                "❌ Une erreur est survenue pendant la génération de la carte.",
                ephemeral=True,
            )

    @app_commands.command(name="xp_serveur", description="Affiche l'XP de tous les membres du serveur")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xp_serveur(self, interaction: discord.Interaction) -> None:
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
        report = "
".join(lines)
        if len(report) < 1900:
            await safe_respond(interaction, f"```
{report}
```", ephemeral=True)
        else:
            file = discord.File(io.StringIO(report), filename="xp_serveur.txt")
            await safe_respond(interaction, "📄 Liste XP en pièce jointe.", ephemeral=True, file=file)

async def setup(bot: commands.Bot) -> None:
    await xp_bootstrap_cache()
    await bot.add_cog(XPCog(bot))
