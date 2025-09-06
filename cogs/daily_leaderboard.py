"""Classement quotidien : attribution de rôles et annonce des gagnants."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from typing import Any, Dict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    ANNOUNCE_CHANNEL_ID,
    DATA_DIR,
    ENABLE_DAILY_AWARDS,
    GUILD_ID,
    MVP_ROLE_ID,
    TOP_MSG_ROLE_ID,
    TOP_VC_ROLE_ID,
)
from utils.timezones import PARIS_TZ
from utils.persistence import read_json_safe, atomic_write_json_async, ensure_dir
from utils.interactions import safe_respond

from cogs.xp import DAILY_STATS, DAILY_LOCK, save_daily_stats_to_disk

logger = logging.getLogger(__name__)

DAILY_WINNERS_FILE = os.path.join(DATA_DIR, "daily_winners.json")
ensure_dir(DATA_DIR)


class DailyLeaderboard(commands.Cog):
    """Calcule et publie le classement quotidien."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_task = asyncio.create_task(self._startup_recovery())
        if ENABLE_DAILY_AWARDS:
            self.daily_reset.start()

    def cog_unload(self) -> None:  # pragma: no cover - cleanup
        if self.daily_reset.is_running():
            self.daily_reset.cancel()
        self._startup_task.cancel()

    async def _startup_recovery(self) -> None:
        await self.bot.wait_until_ready()
        today = datetime.now(PARIS_TZ).date()
        async with DAILY_LOCK:
            pending = [
                day
                for day in DAILY_STATS.keys()
                if datetime.fromisoformat(day).date() < today
            ]
        for day in sorted(pending):
            try:
                data = await self._calculate_daily_winners(day)
                if data:
                    await self._save_winners(day, data)
            except Exception:
                logger.exception("[daily_leaderboard] Échec récupération pour %s", day)

    @tasks.loop(time=time(hour=0, minute=1, tzinfo=PARIS_TZ))
    async def daily_reset(self) -> None:
        """Tâche quotidienne qui calcule les gagnants du jour précédent."""
        day = (datetime.now(PARIS_TZ) - timedelta(days=1)).date().isoformat()
        data = await self._calculate_daily_winners(day)
        if not data:
            return
        await self._save_winners(day, data)
        if ENABLE_DAILY_AWARDS:
            guild = self.bot.get_guild(GUILD_ID) if GUILD_ID else (self.bot.guilds[0] if self.bot.guilds else None)
            if guild:
                await self._update_daily_roles(guild, data["winners"])
            await self._announce_winners(data)

    @daily_reset.before_loop
    async def before_daily_reset(self) -> None:  # pragma: no cover - startup
        await self.bot.wait_until_ready()

    async def _save_winners(self, day: str, data: Dict[str, Any]) -> None:
        existing = read_json_safe(DAILY_WINNERS_FILE)
        existing[day] = data
        try:
            await atomic_write_json_async(DAILY_WINNERS_FILE, existing)
        except OSError as e:  # pragma: no cover - log
            logger.exception("[daily_leaderboard] Échec sauvegarde gagnants: %s", e)

    async def _calculate_daily_winners(self, date: str) -> Dict[str, Any] | None:
        """Calcule les gagnants à partir des statistiques journalières."""
        async with DAILY_LOCK:
            stats = DAILY_STATS.pop(date, None)
        await save_daily_stats_to_disk()
        if not stats:
            logger.info("[daily_leaderboard] Aucune statistique pour %s", date)
            return None
        msg_sorted = sorted(stats.items(), key=lambda x: x[1].get("messages", 0), reverse=True)
        vc_sorted = sorted(stats.items(), key=lambda x: x[1].get("voice", 0), reverse=True)

        def score(item: tuple[str, Dict[str, int]]) -> float:
            s = item[1]
            return s.get("messages", 0) + s.get("voice", 0) / 60.0

        mvp_sorted = sorted(stats.items(), key=score, reverse=True)

        top_msg = [
            {"id": int(uid), "count": int(data.get("messages", 0))}
            for uid, data in msg_sorted[:3]
        ]
        top_vc = [
            {"id": int(uid), "minutes": int(data.get("voice", 0) // 60)}
            for uid, data in vc_sorted[:3]
        ]
        top_mvp = [
            {
                "id": int(uid),
                "score": round(score((uid, data)), 2),
                "messages": int(data.get("messages", 0)),
                "voice": int(data.get("voice", 0) // 60),
            }
            for uid, data in mvp_sorted[:3]
        ]
        winners = {
            "msg": top_msg[0]["id"] if top_msg else None,
            "vc": top_vc[0]["id"] if top_vc else None,
            "mvp": top_mvp[0]["id"] if top_mvp else None,
        }
        return {"top3": {"msg": top_msg, "vc": top_vc, "mvp": top_mvp}, "winners": winners}

    async def _update_daily_roles(self, guild: discord.Guild, winners: Dict[str, int | None]) -> None:
        """Réinitialise et attribue les rôles quotidiens."""
        roles = {
            "msg": guild.get_role(TOP_MSG_ROLE_ID),
            "vc": guild.get_role(TOP_VC_ROLE_ID),
            "mvp": guild.get_role(MVP_ROLE_ID),
        }
        for member in guild.members:
            to_remove = [r for r in roles.values() if r and r in member.roles]
            if to_remove:
                try:
                    await member.remove_roles(*to_remove, reason="Réinitialisation des rôles journaliers")
                except discord.Forbidden:
                    logger.warning("[daily_leaderboard] Permissions insuffisantes pour retirer un rôle")
                except discord.HTTPException as e:
                    logger.error("[daily_leaderboard] Erreur HTTP lors du retrait d'un rôle: %s", e)
                except Exception:
                    logger.exception("[daily_leaderboard] Erreur inattendue lors du retrait d'un rôle")
        for key, uid in winners.items():
            role = roles.get(key)
            if not role or not uid:
                continue
            member = guild.get_member(int(uid))
            if not member:
                continue
            try:
                await member.add_roles(role, reason="Attribution classement quotidien")
            except discord.Forbidden:
                logger.warning("[daily_leaderboard] Permissions insuffisantes pour attribuer un rôle")
            except discord.HTTPException as e:
                logger.error("[daily_leaderboard] Erreur HTTP lors de l'attribution: %s", e)
            except Exception:
                logger.exception("[daily_leaderboard] Erreur inattendue lors de l'attribution du rôle")

    async def _announce_winners(self, data: Dict[str, Any]) -> None:
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if not channel:
            logger.warning("[daily_leaderboard] Salon %s introuvable", ANNOUNCE_CHANNEL_ID)
            return
        winners = data.get("winners", {})
        lines = ["🏆 **Gagnants du jour**", ""]
        lines.append(
            f"👑 MVP : <@{winners['mvp']}>" if winners.get("mvp") else "👑 MVP : Aucun"
        )
        lines.append(
            f"📜 Top messages : <@{winners['msg']}>" if winners.get("msg") else "📜 Top messages : Aucun"
        )
        lines.append(
            f"🎤 Top vocal : <@{winners['vc']}>" if winners.get("vc") else "🎤 Top vocal : Aucun"
        )
        try:
            await channel.send("\n".join(lines))
        except discord.Forbidden:
            logger.warning("[daily_leaderboard] Permissions insuffisantes pour envoyer l'annonce")
        except discord.HTTPException as e:
            logger.error("[daily_leaderboard] Erreur HTTP lors de l'annonce: %s", e)
        except Exception:
            logger.exception("[daily_leaderboard] Erreur inattendue lors de l'annonce")

    @app_commands.command(name="classement_jour", description="Affiche le classement du jour en cours")
    async def classement_jour(self, interaction: discord.Interaction) -> None:
        today = datetime.now(PARIS_TZ).date().isoformat()
        async with DAILY_LOCK:
            stats = dict(DAILY_STATS.get(today, {}))
        if not stats:
            await safe_respond(interaction, "Aucune activité aujourd'hui.", ephemeral=True)
            return
        msg_sorted = sorted(stats.items(), key=lambda x: x[1].get("messages", 0), reverse=True)
        vc_sorted = sorted(stats.items(), key=lambda x: x[1].get("voice", 0), reverse=True)

        def score(item: tuple[str, Dict[str, int]]) -> float:
            s = item[1]
            return s.get("messages", 0) + s.get("voice", 0) / 60.0

        mvp_sorted = sorted(stats.items(), key=score, reverse=True)
        lines = ["🏆 **Classement du jour**", ""]
        if msg_sorted:
            top = msg_sorted[:3]
            lines.append("📜 Messages :" + ", ".join(f"<@{uid}> ({data.get('messages',0)})" for uid, data in top))
        if vc_sorted:
            top = vc_sorted[:3]
            lines.append("🎤 Vocal :" + ", ".join(f"<@{uid}> ({int(data.get('voice',0)//60)}m)" for uid, data in top))
        if mvp_sorted:
            top = mvp_sorted[:3]
            lines.append("👑 MVP :" + ", ".join(f"<@{uid}> ({round(score((uid,data)),2)})" for uid, data in top))
        await safe_respond(interaction, "\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailyLeaderboard(bot))
