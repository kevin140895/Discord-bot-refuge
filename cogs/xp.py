import asyncio
import io
import logging
import os
import random
from datetime import datetime, timezone, time, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    DATA_DIR,
    LOBBY_TEXT_CHANNEL,
    MVP_ROLE_ID,
    TOP_MSG_ROLE_ID,
    TOP_VC_ROLE_ID,
)
from utils.interactions import safe_respond
from utils.persist import atomic_write_json, read_json_safe, ensure_dir
from utils.persistence import schedule_checkpoint
from utils.metrics import measure
from storage.xp_store import xp_store

# Fichiers de persistance
VOICE_TIMES_FILE = os.path.join(DATA_DIR, "voice_times.json")
DAILY_STATS_FILE = os.path.join(DATA_DIR, "daily_stats.json")

# S'assurer que le répertoire de données existe
ensure_dir(DATA_DIR)

# Caches en mémoire
voice_times: dict[str, datetime] = {}
XP_CACHE: dict[str, dict] = xp_store.data
DAILY_STATS: dict[str, dict[str, dict[str, int]]] = {}
XP_LOCK = xp_store.lock
DAILY_LOCK = asyncio.Lock()


def load_voice_times() -> dict[str, datetime]:
    data = read_json_safe(VOICE_TIMES_FILE)
    out: dict[str, datetime] = {}
    for uid, iso in data.items():
        try:
            out[uid] = datetime.fromisoformat(iso)
        except Exception as e:
            logging.warning("Invalid voice time for user %s: %s", uid, e)
            continue
    return out


async def save_voice_times_to_disk() -> None:
    """Sauvegarde atomique des temps vocaux sans bloquer l'event loop."""
    try:
        serializable = {uid: dt.astimezone(timezone.utc).isoformat() for uid, dt in voice_times.items()}
        await asyncio.to_thread(atomic_write_json, VOICE_TIMES_FILE, serializable)
        logging.info("[xp] Voice times sauvegardés (%s)", VOICE_TIMES_FILE)
    except Exception as e:
        logging.exception("[xp] Échec sauvegarde voice times: %s", e)


def load_daily_stats() -> dict:
    return read_json_safe(DAILY_STATS_FILE)


async def save_daily_stats_to_disk() -> None:
    async with DAILY_LOCK:
        data = DAILY_STATS
    await asyncio.to_thread(atomic_write_json, DAILY_STATS_FILE, data)


async def xp_bootstrap_cache() -> None:
    global XP_CACHE, voice_times, DAILY_STATS, XP_LOCK
    XP_CACHE = xp_store.data
    XP_LOCK = xp_store.lock
    voice_times = load_voice_times()
    DAILY_STATS = load_daily_stats()
    logging.info("🎒 XP cache chargé (%d utilisateurs).", len(XP_CACHE))


async def xp_flush_cache_to_disk() -> None:
    await xp_store.flush()
    logging.info("💾 XP flush vers disque (%d utilisateurs).", len(xp_store.data))

async def award_xp(user_id: int, amount: int) -> tuple[int, int, int]:
    """Ajoute ``amount`` d'XP à ``user_id`` via le :class:`XPStore`."""
    return await xp_store.add_xp(user_id, amount)

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
        self.daily_awards.start()
        self._message_cooldown = commands.CooldownMapping.from_cooldown(
            1, 60.0, commands.BucketType.user
        )

    def cog_unload(self) -> None:
        self.auto_backup_xp.cancel()
        self.daily_awards.cancel()

    @tasks.loop(minutes=10)
    async def auto_backup_xp(self) -> None:
        await xp_flush_cache_to_disk()
        try:
            await save_voice_times_to_disk()
        except Exception as e:
            logging.exception("[xp] auto_backup_xp: exception: %s", e)
        await save_daily_stats_to_disk()
        logging.info("🛟 Sauvegarde périodique effectuée.")

    @tasks.loop(time=time(hour=0, tzinfo=timezone.utc))
    async def daily_awards(self) -> None:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        async with DAILY_LOCK:
            stats = DAILY_STATS.get(yesterday, {})
        if not stats:
            return

        def top_user(key: str) -> str | None:
            if not stats:
                return None
            return max(stats.items(), key=lambda x: x[1].get(key, 0))[0]

        top_msg = top_user("messages")
        top_vc = top_user("voice")
        top_mvp = max(
            stats.items(),
            key=lambda x: x[1].get("messages", 0) + x[1].get("voice", 0) // 60,
        )[0]

        guild = self.bot.guilds[0] if self.bot.guilds else None
        if guild:
            roles = {
                "mvp": guild.get_role(MVP_ROLE_ID),
                "msg": guild.get_role(TOP_MSG_ROLE_ID),
                "vc": guild.get_role(TOP_VC_ROLE_ID),
            }
            for member in guild.members:
                to_remove = [r for r in roles.values() if r and r in member.roles]
                if to_remove:
                    try:
                        await member.remove_roles(*to_remove, reason="Remise à zéro du classement quotidien")
                    except Exception:
                        pass
            winners = {
                "mvp": guild.get_member(int(top_mvp)) if top_mvp else None,
                "msg": guild.get_member(int(top_msg)) if top_msg else None,
                "vc": guild.get_member(int(top_vc)) if top_vc else None,
            }
            if winners["mvp"] and roles["mvp"]:
                await winners["mvp"].add_roles(roles["mvp"], reason="👑 MVP du Refuge")
            if winners["msg"] and roles["msg"]:
                await winners["msg"].add_roles(roles["msg"], reason="📜 Écrivain du Refuge")
            if winners["vc"] and roles["vc"]:
                await winners["vc"].add_roles(roles["vc"], reason="🎤 Voix du Refuge")
            channel = guild.get_channel(LOBBY_TEXT_CHANNEL)
            if channel:
                await channel.send(
                    (
                        "🎉 Félicitations aux champions du Refuge ! 🎉\n\n"
                        "Chaque jour, nous mettons à l’honneur nos membres les plus actifs.\n"
                        "Les Top 1 de chaque catégorie reçoivent un rôle spécial, valable du moment du message (00h00) jusqu’à 23h59 :\n\n"
                        f"👑 MVP du Refuge **{winners['mvp'].mention if winners['mvp'] else 'Personne'}**\n"
                        f"📜 Écrivain du Refuge **{winners['msg'].mention if winners['msg'] else 'Personne'}**\n"
                        f"🎤 Voix du Refuge **{winners['vc'].mention if winners['vc'] else 'Personne'}**\n\n"
                        "👏 Bravo aux gagnants du jour, continuez à faire vivre le Refuge !"
                    )
                )

        async with DAILY_LOCK:
            DAILY_STATS.pop(yesterday, None)
        await save_daily_stats_to_disk()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        # Statistiques quotidiennes
        today = datetime.now(timezone.utc).date().isoformat()
        async with DAILY_LOCK:
            day = DAILY_STATS.setdefault(today, {})
            user = day.setdefault(str(message.author.id), {"messages": 0, "voice": 0})
            user["messages"] = int(user.get("messages", 0)) + 1
        await schedule_checkpoint(save_daily_stats_to_disk)

        bucket = self._message_cooldown.get_bucket(message)
        if bucket.update_rate_limit():
            return
        amount = random.randint(5, 15)
        await award_xp(message.author.id, amount)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Ignorer si l'utilisateur ne change pas réellement de salon
        if before.channel == after.channel:
            await schedule_checkpoint(save_voice_times_to_disk)
            return

        now = datetime.now(timezone.utc)
        uid = str(member.id)

        # Déconnexion ou changement de salon : calculer la durée et attribuer l'XP
        if before.channel is not None:
            start = voice_times.pop(uid, None)
            if start is not None:
                duration = now - start
                xp_amount = int(duration.total_seconds() // 60)
                await award_xp(member.id, xp_amount)
                # Statistiques quotidiennes (en secondes)
                day = now.date().isoformat()
                async with DAILY_LOCK:
                    d = DAILY_STATS.setdefault(day, {})
                    u = d.setdefault(uid, {"messages": 0, "voice": 0})
                    u["voice"] = int(u.get("voice", 0)) + int(duration.total_seconds())
                await schedule_checkpoint(save_daily_stats_to_disk)

        # Connexion à un nouveau salon
        if after.channel is not None:
            voice_times[uid] = now

        await schedule_checkpoint(save_voice_times_to_disk)

    @auto_backup_xp.before_loop
    async def before_auto_backup_xp(self) -> None:
        await self.bot.wait_until_ready()

    @daily_awards.before_loop
    async def before_daily_awards(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="rang", description="Affiche ton niveau avec une carte graphique")
    async def rang(self, interaction: discord.Interaction) -> None:
        with measure("slash:rang"):
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
        with measure("slash:xp_serveur"):
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
            report = '\n'.join(lines)
            if len(report) < 1900:
                await safe_respond(interaction, f"```\n{report}\n```", ephemeral=True)
            else:
                file = discord.File(io.StringIO(report), filename="xp_serveur.txt")
                await safe_respond(interaction, "📄 Liste XP en pièce jointe.", ephemeral=True, file=file)

async def setup(bot: commands.Bot) -> None:
    await xp_bootstrap_cache()
    await bot.add_cog(XPCog(bot))
