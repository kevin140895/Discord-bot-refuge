"""Classement quotidien : annonce des gagnants."""

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
)
from utils.timezones import PARIS_TZ
from utils.persistence import read_json_safe, atomic_write_json_async, ensure_dir
from utils.interactions import safe_respond

from cogs import daily_ranking
from cogs.xp import DAILY_STATS, DAILY_LOCK

logger = logging.getLogger(__name__)

DAILY_WINNERS_FILE = os.path.join(DATA_DIR, "daily_winners.json")
# Maximum duration (in seconds) to wait for the ranking cog before giving up.
RANKING_WAIT_TIMEOUT = 15.0
ensure_dir(DATA_DIR)


class DailyLeaderboard(commands.Cog):
    """Calcule et publie le classement quotidien."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        existing = read_json_safe(DAILY_WINNERS_FILE)
        self._known_winners: set[str] = set(existing.keys())
        self._startup_task = asyncio.create_task(self._startup_recovery())
        self._ranking_listener = asyncio.create_task(self._listen_for_rankings())
        if ENABLE_DAILY_AWARDS:
            self.daily_reset.start()

    def cog_unload(self) -> None:  # pragma: no cover - cleanup
        if self.daily_reset.is_running():
            self.daily_reset.cancel()
        self._startup_task.cancel()
        if hasattr(self, "_ranking_listener"):
            self._ranking_listener.cancel()

    async def _startup_recovery(self) -> None:
        await self.bot.wait_until_ready()
        today = datetime.now(PARIS_TZ).date()
        try:
            cached = await daily_ranking.list_cached_rankings()
        except Exception:
            cached = set()

        for day in sorted(cached):
            if day in self._known_winners:
                continue
            try:
                if datetime.fromisoformat(day).date() >= today:
                    continue
            except ValueError:
                continue
            ranking = daily_ranking.get_cached_ranking(day)
            if not ranking:
                continue
            try:
                await self._save_winners(
                    day,
                    {
                        "top3": ranking.get("top3", {}),
                        "winners": ranking.get("winners", {}),
                    },
                )
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
            await self._announce_winners(data)

    @daily_reset.before_loop
    async def before_daily_reset(self) -> None:  # pragma: no cover - startup
        await self.bot.wait_until_ready()

    async def _save_winners(self, day: str, data: Dict[str, Any]) -> None:
        existing = read_json_safe(DAILY_WINNERS_FILE)
        existing[day] = data
        try:
            await atomic_write_json_async(DAILY_WINNERS_FILE, existing)
            if not hasattr(self, "_known_winners"):
                self._known_winners = set()
            self._known_winners.add(day)
        except OSError as e:  # pragma: no cover - log
            logger.exception("[daily_leaderboard] Échec sauvegarde gagnants: %s", e)

    async def _listen_for_rankings(self) -> None:
        try:
            await self.bot.wait_until_ready()
            startup = getattr(self, "_startup_task", None)
            if startup is not None:
                await asyncio.shield(startup)
        except asyncio.CancelledError:  # pragma: no cover - teardown
            return
        except Exception:  # pragma: no cover - log and continue
            logger.exception("[daily_leaderboard] Échec attente démarrage")
        seen = set(getattr(self, "_known_winners", set()))
        while True:
            try:
                updates = await daily_ranking.wait_for_new_rankings(seen, timeout=None)
            except asyncio.CancelledError:  # pragma: no cover - teardown
                return
            except Exception:
                logger.exception("[daily_leaderboard] Échec attente classement")
                await asyncio.sleep(5)
                continue
            if not updates:
                continue
            seen.update(updates.keys())
            today = datetime.now(PARIS_TZ).date()
            for day, ranking in sorted(updates.items()):
                try:
                    if datetime.fromisoformat(day).date() >= today:
                        continue
                except ValueError:
                    continue
                if day in getattr(self, "_known_winners", set()):
                    continue
                try:
                    await self._save_winners(
                        day,
                        {
                            "top3": ranking.get("top3", {}),
                            "winners": ranking.get("winners", {}),
                        },
                    )
                except Exception:
                    logger.exception("[daily_leaderboard] Échec sauvegarde auto pour %s", day)

    async def _calculate_daily_winners(self, date: str) -> Dict[str, Any] | None:
        """Calcule les gagnants à partir des statistiques journalières."""
        ranking = await daily_ranking.wait_for_ranking(date, timeout=RANKING_WAIT_TIMEOUT)
        if not ranking:
            logger.info("[daily_leaderboard] Classement absent pour %s", date)
            return None
        top3 = ranking.get("top3", {})
        winners = ranking.get("winners", {})
        return {"top3": top3, "winners": winners}


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
