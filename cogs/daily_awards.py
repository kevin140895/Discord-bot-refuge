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
    MVP_ROLE_ID,
    WRITER_ROLE_ID,
    VOICE_ROLE_ID,
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


def _format_hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


class DailyAwards(commands.Cog):
    """Publie l'annonce des gagnants et attribue les rôles."""

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
        return read_json_safe(DAILY_AWARD_FILE)

    def _write_state(self, data: Dict[str, Any]) -> None:
        atomic_write_json(DAILY_AWARD_FILE, data)

    # ── Role management ─────────────────────────────────────
    async def _reset_and_assign(self, winners: Dict[str, int | None]) -> None:
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning("[daily_awards] Guilde %s introuvable", GUILD_ID)
            return
        roles = {
            "mvp": guild.get_role(MVP_ROLE_ID),
            "msg": guild.get_role(WRITER_ROLE_ID),
            "vc": guild.get_role(VOICE_ROLE_ID),
        }
        for member in guild.members:
            to_remove = [r for r in roles.values() if r and r in member.roles]
            if to_remove:
                try:
                    await member.remove_roles(
                        *to_remove,
                        reason="Réinitialisation des rôles journaliers",
                    )
                except discord.Forbidden:
                    logger.warning("[daily_awards] Permissions insuffisantes pour retirer un rôle")
                except discord.NotFound:
                    logger.warning("[daily_awards] Rôle ou membre introuvable lors du retrait")
                except discord.HTTPException as e:
                    logger.error("[daily_awards] Erreur HTTP lors du retrait d'un rôle: %s", e)
                except Exception as e:  # pragma: no cover - just log
                    logger.exception("[daily_awards] Erreur inattendue lors du retrait: %s", e)
        for key, uid in winners.items():
            role = roles.get(key)
            if not role or not uid:
                continue
            member = guild.get_member(int(uid))
            if not member:
                continue
            try:
                await member.add_roles(role, reason="Attribution classement quotidien")
                logger.info("[daily_awards] Rôle %s attribué à %s", role.id, uid)
            except discord.Forbidden:
                logger.warning("[daily_awards] Permissions insuffisantes pour attribuer un rôle")
            except discord.NotFound:
                logger.warning("[daily_awards] Rôle ou membre introuvable lors de l'attribution")
            except discord.HTTPException as e:
                logger.error("[daily_awards] Erreur HTTP lors de l'attribution du rôle: %s", e)
            except Exception as e:  # pragma: no cover
                logger.exception("[daily_awards] Erreur inattendue lors de l'attribution: %s", e)

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
                    f"Rôle attribué : <@&{MVP_ROLE_ID}>",
                    f"• Points combinés : {mvp_points}  (messages : {mvp_msgs} · vocal : {mvp_voice})",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "👑 **MVP du Refuge** — Aucun gagnant aujourd’hui",
                    f"Rôle non attribué : <@&{MVP_ROLE_ID}>",
                    "",
                ]
            )

        if writer:
            writer_entry = writer[0]
            writer_mention = await self._mention_or_name(writer_entry["id"])
            writer_msgs = writer_entry["count"]
            lines.extend(
                [
                    f"📜 **Écrivain du Refuge** — {writer_mention}",
                    f"Rôle attribué : <@&{WRITER_ROLE_ID}>",
                    f"• Messages envoyés : {writer_msgs}",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "📜 **Écrivain du Refuge** — Aucun gagnant aujourd’hui",
                    f"Rôle non attribué : <@&{WRITER_ROLE_ID}>",
                    "",
                ]
            )

        if voice:
            voice_entry = voice[0]
            voice_mention = await self._mention_or_name(voice_entry["id"])
            voice_time = _format_hm(voice_entry["minutes"])
            lines.extend(
                [
                    f"🎤 **Voix du Refuge** — {voice_mention}",
                    f"Rôle attribué : <@&{VOICE_ROLE_ID}>",
                    f"• Temps en vocal : {voice_time}",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "🎤 **Voix du Refuge** — Aucun gagnant aujourd’hui",
                    f"Rôle non attribué : <@&{VOICE_ROLE_ID}>",
                    "",
                ]
            )

        lines.extend(
            [
                "⏳ **Durée des rôles** : aujourd’hui 00:00 ➜ 23:59",
                "Félicitations aux gagnants ! Continuez à participer pour tenter le titre demain 🎉",
            ]
        )
        return "\n".join(lines)

    async def _maybe_award(self, data: Dict[str, Any]) -> None:
        if not data:
            return
        winners = data.get("winners") or {}
        if not winners:
            logger.warning("[daily_awards] Pas de données gagnants pour %s", data.get("date"))
        channel = self.bot.get_channel(AWARD_ANNOUNCE_CHANNEL_ID)
        winners = data.get("winners") or {}
        if not winners:
            logger.warning("[daily_awards] Pas de données gagnants pour %s", data.get("date"))
        channel = self.bot.get_channel(AWARD_ANNOUNCE_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(AWARD_ANNOUNCE_CHANNEL_ID)
            except Exception:
                logger.error(
                    "[daily_awards] Salon %s introuvable", AWARD_ANNOUNCE_CHANNEL_ID
                )
                return
        state = self._read_state()
        date = data.get("date")
        if state.get("date") == date and state.get("message_id"):
            try:
                await channel.fetch_message(state["message_id"])
                return
            except discord.NotFound:
                logger.warning(
                    "[daily_awards] Message %s introuvable, nouvelle publication",
                    state["message_id"],
                )
        await self._reset_and_assign(winners)
        message = await self._build_message(data)
        if not message:
            logger.warning("[daily_awards] Message vide pour %s", date)
            return
        msg = await channel.send(message)
        self._write_state({"date": date, "message_id": msg.id})
        logger.info("[daily_awards] Annonce %s publiée", date)

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
        # calculer le classement précédent.  Pour éviter de rater
        # l'attribution des rôles, on patiente quelques instants et on
        # réessaie tant que le fichier de classement ne contient pas les
        # gagnants attendus.
        for _ in range(5):
            data = read_json_safe(DAILY_RANK_FILE)
            if data.get("winners"):
                await self._maybe_award(data)
                return
            await asyncio.sleep(2)
        logger.warning("[daily_awards] Classement introuvable au démarrage")


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailyAwards(bot))
