"""Gestion du classement quotidien.

Ce module calcule les gagnants quotidiens à partir des statistiques
d'activité et persiste les résultats dans ``daily_ranking.json`` via
``utils.persistence``. Il lit et écrit également les statistiques
journalières partagées avec la cog XP.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, time, timezone
from typing import Dict, Any
import json

import discord
from discord import app_commands
from discord.ext import commands

from config import DATA_DIR, XP_VIEWER_ROLE_ID
from utils.interactions import safe_respond
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir
from .xp import DAILY_STATS, DAILY_LOCK, save_daily_stats_to_disk
logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback
    PARIS_TZ = timezone.utc


DAILY_RANK_FILE = os.path.join(DATA_DIR, "daily_ranking.json")
ensure_dir(DATA_DIR)


class DailyRankingAndRoles(commands.Cog):
    """Calcul et persistance des classements quotidiens."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._task = asyncio.create_task(self._scheduler())
        self._startup_task = asyncio.create_task(self._startup_check())

    def cog_unload(self) -> None:
        self._task.cancel()
        self._startup_task.cancel()

    # ── Persistence helpers ──────────────────────────────────

    def _read_persistence(self) -> Dict[str, Any]:
        return read_json_safe(DAILY_RANK_FILE)

    def _write_persistence(self, data: Dict[str, Any]) -> None:
        atomic_write_json(DAILY_RANK_FILE, data)

    # ── Computation ──────────────────────────────────────────

    def _compute_ranking(self, stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
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

    # ── Main task ───────────────────────────────────────────

    async def _startup_check(self) -> None:
        await self.bot.wait_until_ready()
        today = datetime.now(PARIS_TZ).date()
        async with DAILY_LOCK:
            pending = [
                day
                for day in DAILY_STATS.keys()
                if datetime.fromisoformat(day).date() < today
            ]
        for day in sorted(pending):
            logger.info("[daily_ranking] Traitement au démarrage pour %s", day)
            async with DAILY_LOCK:
                stats = DAILY_STATS.pop(day, {})
            if not stats:
                logger.info("[daily_ranking] Aucune statistique pour %s", day)
                await save_daily_stats_to_disk()
                continue
            ranking = self._compute_ranking(stats)
            ranking["date"] = day
            self._write_persistence(ranking)
            await save_daily_stats_to_disk()
            logger.info("[daily_ranking] Classement %s sauvegardé", day)

    async def _scheduler(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(PARIS_TZ)
            target = datetime.combine(now.date(), time(hour=0, tzinfo=PARIS_TZ))
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            await self._run_daily_task()

    async def _run_daily_task(self) -> None:
        now = datetime.now(PARIS_TZ)
        day = (now - timedelta(days=1)).date().isoformat()
        logger.info("[daily_ranking] Calcul du classement pour %s", day)
        async with DAILY_LOCK:
            stats = DAILY_STATS.pop(day, {})
        if not stats:
            logger.info("[daily_ranking] Aucune statistique pour %s", day)
            await save_daily_stats_to_disk()
            return
        ranking = self._compute_ranking(stats)
        ranking["date"] = day
        self._write_persistence(ranking)
        await save_daily_stats_to_disk()
        logger.info("[daily_ranking] Classement %s sauvegardé", day)

    @app_commands.command(
        name="test_classement1", description="Prévisualise le classement du jour"
    )
    async def test_classement1(
        self, interaction: discord.Interaction
    ) -> None:
        if not any(r.id == XP_VIEWER_ROLE_ID for r in getattr(interaction.user, "roles", [])):
            await safe_respond(interaction, "Accès refusé.", ephemeral=True)
            return
        data = read_json_safe(DAILY_RANK_FILE)
        if not data:
            await safe_respond(interaction, "Aucun classement disponible.", ephemeral=True)
            return
        await safe_respond(
            interaction,
            f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailyRankingAndRoles(bot))
