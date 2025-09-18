"""Gestion du classement quotidien.

Ce module calcule les gagnants quotidiens à partir des statistiques
d'activité et persiste les résultats dans ``daily_ranking.json`` via
``utils.persistence``. Il lit et écrit également les statistiques
journalières partagées avec la cog XP.
"""

import asyncio
import logging
import os
from copy import deepcopy
from datetime import datetime, timedelta, time, timezone
from typing import Dict, Any

from discord.ext import commands

from config import DATA_DIR
from utils.persistence import read_json_safe, atomic_write_json, ensure_dir
import cogs.xp as xp
logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback
    PARIS_TZ = timezone.utc


DAILY_RANK_FILE = os.path.join(DATA_DIR, "daily_ranking.json")
ensure_dir(DATA_DIR)

_RANKING_CONDITION: asyncio.Condition = asyncio.Condition()
LATEST_RANKINGS: Dict[str, Dict[str, Any]] = {}


def _snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep copy of ``data`` suitable for sharing across cogs."""

    return deepcopy(data)


def _prime_cache_from_disk() -> None:
    data = read_json_safe(DAILY_RANK_FILE)
    date = data.get("date")
    if date:
        LATEST_RANKINGS[date] = _snapshot(data)


_prime_cache_from_disk()


async def _record_ranking_result(day: str, ranking: Dict[str, Any]) -> None:
    """Cache ``ranking`` and wake waiters waiting for ``day``."""

    async with _RANKING_CONDITION:
        LATEST_RANKINGS[day] = _snapshot(ranking)
        _RANKING_CONDITION.notify_all()


async def wait_for_ranking(date: str, *, timeout: float | None = 60.0) -> Dict[str, Any] | None:
    """Wait until ranking data for ``date`` is available."""

    async with _RANKING_CONDITION:
        if date in LATEST_RANKINGS:
            return _snapshot(LATEST_RANKINGS[date])
        if timeout == 0:
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout is not None else None
        while date not in LATEST_RANKINGS:
            if timeout is None:
                await _RANKING_CONDITION.wait()
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(_RANKING_CONDITION.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
        return _snapshot(LATEST_RANKINGS[date])


async def list_cached_rankings() -> set[str]:
    """Return the set of dates for which rankings are currently cached."""

    async with _RANKING_CONDITION:
        return set(LATEST_RANKINGS.keys())


def get_cached_ranking(date: str) -> Dict[str, Any] | None:
    """Return cached ranking data for ``date`` if available."""

    data = LATEST_RANKINGS.get(date)
    if not data:
        return None
    return _snapshot(data)


async def wait_for_new_rankings(
    seen: set[str], *, timeout: float | None = None
) -> Dict[str, Dict[str, Any]]:
    """Wait until new ranking entries (not in ``seen``) are available."""

    async with _RANKING_CONDITION:
        fresh = {day: _snapshot(data) for day, data in LATEST_RANKINGS.items() if day not in seen}
        if fresh or timeout == 0:
            return fresh
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout is not None else None
        while True:
            if timeout is None:
                await _RANKING_CONDITION.wait()
            else:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return {}
                try:
                    await asyncio.wait_for(_RANKING_CONDITION.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return {}
            fresh = {
                day: _snapshot(data) for day, data in LATEST_RANKINGS.items() if day not in seen
            }
            if fresh:
                return fresh


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
        async with xp.DAILY_LOCK:
            pending = [
                day
                for day in xp.DAILY_STATS.keys()
                if datetime.fromisoformat(day).date() < today
            ]
        for day in sorted(pending):
            logger.info("[daily_ranking] Traitement au démarrage pour %s", day)
            async with xp.DAILY_LOCK:
                stats = xp.DAILY_STATS.pop(day, {})
            if not stats:
                logger.info("[daily_ranking] Aucune statistique pour %s", day)
                await xp.save_daily_stats_to_disk()
                continue
            ranking = self._compute_ranking(stats)
            ranking["date"] = day
            self._write_persistence(ranking)
            await _record_ranking_result(day, ranking)
            await xp.save_daily_stats_to_disk()
            logger.info("[daily_ranking] Classement %s sauvegardé", day)

    async def _scheduler(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(PARIS_TZ)
            target = datetime.combine(now.date(), time(hour=0, tzinfo=PARIS_TZ))
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            try:
                await self._run_daily_task()
            except Exception:
                logger.exception("[daily_ranking] Échec de _run_daily_task")

    async def _run_daily_task(self) -> None:
        now = datetime.now(PARIS_TZ)
        day = (now - timedelta(days=1)).date().isoformat()
        logger.info("[daily_ranking] Calcul du classement pour %s", day)
        async with xp.DAILY_LOCK:
            stats = xp.DAILY_STATS.pop(day, {})
        if not stats:
            logger.info("[daily_ranking] Aucune statistique pour %s", day)
            await xp.save_daily_stats_to_disk()
            return
        ranking = self._compute_ranking(stats)
        ranking["date"] = day
        self._write_persistence(ranking)
        await _record_ranking_result(day, ranking)
        await xp.save_daily_stats_to_disk()
        logger.info("[daily_ranking] Classement %s sauvegardé", day)


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - integration
    await bot.add_cog(DailyRankingAndRoles(bot))
