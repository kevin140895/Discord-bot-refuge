"""Annonce des gagnants quotidiens à 00h03."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, time, timezone
from typing import Any, Dict

import discord
from discord.ext import commands

from config import (
    AWARD_ANNOUNCE_CHANNEL_ID,
    GUILD_ID,
    DATA_DIR,
    ENABLE_DAILY_AWARDS,
)
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir
logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback
    PARIS_TZ = timezone.utc

DAILY_RANK_FILE = os.path.join(DATA_DIR, "daily_ranking.json")
DAILY_AWARD_FILE = os.path.join(DATA_DIR, "daily_awards.json")
ensure_dir(DATA_DIR)


def today_str_eu_paris() -> str:
    """Retourne la date du jour au format YYYY-MM-DD en Europe/Paris."""
    return datetime.now(PARIS_TZ).date().isoformat()


def load_last_award_date() -> tuple[str | None, int | None]:
    """Charge la dernière annonce enregistrée."""
    state = read_json_safe(DAILY_AWARD_FILE)
    return state.get("date"), state.get("message_id")


def save_last_award_date(date: str | None, message_id: int | None) -> None:
    """Enregistre la date d'annonce et l'identifiant du message."""
    atomic_write_json(DAILY_AWARD_FILE, {"date": date, "message_id": message_id})


def _format_hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


class DailyAwards(commands.Cog):
    """Publie l'annonce des gagnants."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._task = None
        if ENABLE_DAILY_AWARDS:
            self._task = asyncio.create_task(self._scheduler())
            asyncio.create_task(self._startup_check())

    def cog_unload(self) -> None:  # pragma: no cover - cleanup
        if self._task:
            self._task.cancel()

    # ── Persistence helpers ──────────────────────────────────
    def _read_state(self) -> Dict[str, Any]:
        date, message_id = load_last_award_date()
        data: Dict[str, Any] = {}
        if date:
            data["date"] = date
        if message_id is not None:
            data["message_id"] = message_id
        return data

    def _write_state(self, data: Dict[str, Any]) -> None:
        save_last_award_date(data.get("date"), data.get("message_id"))


    async def _mention_or_name(self, uid: int) -> str:
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning("[daily_awards] Guilde %s introuvable", GUILD_ID)
            return str(uid)
        member = guild.get_member(uid)
        if member:
            return member.mention
        user = self.bot.get_user(uid)
        if user:
            return user.mention
        try:
            user = await self.bot.fetch_user(uid)
            return user.name
        except discord.HTTPException:
            return str(uid)

    async def _get_announce_channel(self) -> discord.abc.Messageable | None:
        """Récupère le salon d'annonce en vérifiant les permissions."""
        channel = self.bot.get_channel(AWARD_ANNOUNCE_CHANNEL_ID)
        guild = self.bot.get_guild(GUILD_ID) if hasattr(self.bot, "get_guild") else None
        if channel is None and guild:
            try:
                channel = await guild.fetch_channel(AWARD_ANNOUNCE_CHANNEL_ID)
            except discord.Forbidden:
                logger.warning(
                    "[daily_awards] Accès refusé au salon %s", AWARD_ANNOUNCE_CHANNEL_ID
                )
            except discord.NotFound:
                logger.warning(
                    "[daily_awards] Salon %s introuvable", AWARD_ANNOUNCE_CHANNEL_ID
                )
            except discord.HTTPException:
                logger.exception(
                    "[daily_awards] Erreur HTTP lors de la récupération du salon %s",
                    AWARD_ANNOUNCE_CHANNEL_ID,
                )
        if channel and hasattr(channel, "send"):
            me = None
            if guild:
                me = getattr(guild, "me", None)
            elif hasattr(channel, "guild"):
                me = getattr(channel.guild, "me", None)
            if me and hasattr(channel, "permissions_for"):
                perms = channel.permissions_for(me)
                if not perms.send_messages:
                    logger.warning(
                        "[daily_awards] Pas la permission d'envoyer dans %s",
                        getattr(channel, "id", "inconnu"),
                    )
                    channel = None
            if channel:
                return channel
        if guild:
            fallback = next(
                (
                    c
                    for c in getattr(guild, "text_channels", [])
                    if c.permissions_for(guild.me).send_messages
                ),
                None,
            )
            if fallback:
                logger.warning(
                    "[daily_awards] Utilisation du salon de secours %s", fallback.id
                )
                return fallback
        logger.error("[daily_awards] Aucun salon texte disponible pour l'annonce")
        return None

    async def _build_message(self, data: Dict[str, Any]) -> str:
        top3 = data.get("top3", {})
        mvp = top3.get("mvp") or []
        writer = top3.get("msg") or []
        voice = top3.get("vc") or []

        lines = ["📢 **Annonce des gagnants — classement de 00h00**", ""]

        if mvp:
            mvp_entry = mvp[0]
            mvp_mention = await self._mention_or_name(mvp_entry["id"])
            mvp_points = mvp_entry["score"]
            mvp_msgs = mvp_entry["messages"]
            mvp_voice = _format_hm(mvp_entry["voice"])
            lines.extend(
                [
                    f"👑 **MVP du Refuge** — {mvp_mention}",
                    f"• Points combinés : {mvp_points}  (messages : {mvp_msgs} · vocal : {mvp_voice})",
                    "",
                ]
            )
        else:
            lines.extend([
                "👑 **MVP du Refuge** — Aucun gagnant aujourd’hui",
                "",
            ])

        if writer:
            writer_entry = writer[0]
            writer_mention = await self._mention_or_name(writer_entry["id"])
            writer_msgs = writer_entry["count"]
            lines.extend([
                f"📜 **Écrivain du Refuge** — {writer_mention}",
                f"• Messages envoyés : {writer_msgs}",
                "",
            ])
        else:
            lines.extend([
                "📜 **Écrivain du Refuge** — Aucun gagnant aujourd’hui",
                "",
            ])

        if voice:
            voice_entry = voice[0]
            voice_mention = await self._mention_or_name(voice_entry["id"])
            voice_time = _format_hm(voice_entry["minutes"])
            lines.extend([
                f"🎤 **Voix du Refuge** — {voice_mention}",
                f"• Temps en vocal : {voice_time}",
                "",
            ])
        else:
            lines.extend([
                "🎤 **Voix du Refuge** — Aucun gagnant aujourd’hui",
                "",
            ])

        lines.append(
            "Félicitations aux gagnants ! Continuez à participer pour tenter le titre demain 🎉"
        )
        return "\n".join(lines)

    async def _maybe_award(self, data: Dict[str, Any]) -> None:
        if not data:
            return
        today = today_str_eu_paris()
        logger.info("[daily_awards] Début annonce du %s", today)

        state = self._read_state()
        if state.get("date") == today:
            logger.info("[daily_awards] Déjà annoncé pour aujourd'hui")
            return

        channel = await self._get_announce_channel()
        if channel is None:
            return

        message = await self._build_message(data)
        if not message:
            logger.warning("[daily_awards] Message vide pour %s", today)
            return

        try:
            msg = await channel.send(message)
        except discord.Forbidden:
            logger.warning("[daily_awards] Permissions insuffisantes pour envoyer l'annonce")
            return
        except discord.NotFound:
            logger.warning("[daily_awards] Salon introuvable lors de l'envoi de l'annonce")
            return
        except discord.HTTPException:
            logger.exception("[daily_awards] Erreur HTTP lors de l'envoi de l'annonce")
            return

        self._write_state({"date": today, "message_id": getattr(msg, "id", None)})
        logger.info("[daily_awards] Annonce %s publiée", today)

    # ── Tasks ────────────────────────────────────────────────
    async def _scheduler(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(PARIS_TZ)
            target = datetime.combine(
                now.date(), time(hour=0, minute=3, tzinfo=PARIS_TZ)
            )
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            for _ in range(10):
                data = read_json_safe(DAILY_RANK_FILE)
                if data.get("winners"):
                    try:
                        await self._maybe_award(data)
                    except Exception:
                        logger.exception("[daily_awards] Échec de _maybe_award")
                    break

    async def _startup_check(self) -> None:
        await self.bot.wait_until_ready()
        # Au démarrage, ``daily_ranking`` peut encore être en train de
        # calculer le classement précédent. Pour éviter de rater
        # l'annonce, on patiente quelques instants et on réessaie tant que
        # le fichier de classement ne contient pas les gagnants attendus.
        for _ in range(5):
            data = read_json_safe(DAILY_RANK_FILE)
            if data.get("winners"):
                await self._maybe_award(data)
                return
            await asyncio.sleep(2)
        logger.warning("[daily_awards] Classement introuvable au démarrage")


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailyAwards(bot))
