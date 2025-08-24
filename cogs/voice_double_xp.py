"""Gestion des sessions Double XP vocal.

Tire quotidiennement jusqu'à deux créneaux horaires aléatoires et active
un multiplicateur ×2 sur l'XP vocal pendant une heure avec annonces de début
et de fin dans un salon dédié.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import date, datetime, time, timedelta
from typing import List, Dict, Any

from discord.ext import commands, tasks

from config import (
    DATA_DIR,
    XP_DOUBLE_VOICE_SESSIONS_PER_DAY,
    XP_DOUBLE_VOICE_DURATION_MINUTES,
    XP_DOUBLE_VOICE_START_HOUR,
    XP_DOUBLE_VOICE_END_HOUR,
    XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID,
)
from utils.persistence import read_json_safe, atomic_write_json_async, ensure_dir
from utils.voice_bonus import set_voice_bonus
logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback when zoneinfo is missing
    from datetime import timezone

    PARIS_TZ = timezone.utc

STATE_FILE = os.path.join(DATA_DIR, "double_voice_xp.json")
ensure_dir(DATA_DIR)


def _read_state() -> dict:
    """Read persisted state from disk.

    Retourne un dictionnaire vide en cas d'erreur et journalise
    l'exception.
    """
    try:
        return read_json_safe(STATE_FILE)
    except Exception:  # pragma: no cover - unexpected error
        logger.exception("[double_xp] failed to read state file")
        return {}


async def _write_state(data: dict) -> None:
    """Persist ``data`` to disk and log failures."""
    try:
        await atomic_write_json_async(STATE_FILE, data)
    except Exception:  # pragma: no cover - disk errors
        logger.exception("[double_xp] failed to write state file")


def _random_sessions() -> List[str]:
    """Return a list of random HH:MM sessions for today."""
    count = random.randint(0, XP_DOUBLE_VOICE_SESSIONS_PER_DAY)
    if count == 0:
        return []
    start_min = XP_DOUBLE_VOICE_START_HOUR * 60
    end_min = XP_DOUBLE_VOICE_END_HOUR * 60 - XP_DOUBLE_VOICE_DURATION_MINUTES
    if end_min < start_min:
        end_min = start_min
    minutes = random.sample(range(start_min, end_min + 1), count)
    minutes.sort()
    return [f"{m // 60:02d}:{m % 60:02d}" for m in minutes]


def _hm_to_dt(hm: str, day: date) -> datetime:
    """Convertit une heure ``HH:MM`` en :class:`datetime` pour ``day``."""
    h, m = map(int, hm.split(":"))
    return datetime.combine(day, time(hour=h, minute=m, tzinfo=PARIS_TZ))


class DoubleVoiceXP(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tasks: List[asyncio.Task] = []
        self.state: Dict[str, Any] = {}
        self.daily_planner.start()
        self.bot.loop.create_task(self._startup())

    async def _startup(self) -> None:
        """Attendre le démarrage du bot et préparer les sessions du jour."""
        await self.bot.wait_until_ready()
        await self._prepare_today()

    def cog_unload(self) -> None:  # pragma: no cover - cleanup
        self.daily_planner.cancel()
        for task in self._tasks:
            task.cancel()

    @tasks.loop(time=time(hour=0, minute=1, tzinfo=PARIS_TZ))
    async def daily_planner(self) -> None:
        await self._prepare_today(force=True)

    @daily_planner.before_loop
    async def before_daily_planner(self) -> None:  # pragma: no cover - simple wait
        await self.bot.wait_until_ready()

    async def _prepare_today(self, force: bool = False) -> None:
        """Lire/initialiser l'état du jour puis planifier ou reprendre les sessions."""

        # Cancel any previously scheduled tasks to avoid duplicates.
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        today = datetime.now(PARIS_TZ).date()
        state = _read_state()
        if force or state.get("date") != today.isoformat():
            sessions = [
                {"hm": hm, "started": False, "end": None, "ended": False}
                for hm in _random_sessions()
            ]
            state = {"date": today.isoformat(), "sessions": sessions}
            await _write_state(state)
        else:
            sessions = state.get("sessions", [])
            if sessions and isinstance(sessions[0], str):  # rétro-compatibilité
                sessions = [
                    {"hm": hm, "started": False, "end": None, "ended": False}
                    for hm in sessions
                ]
                state["sessions"] = sessions
                await _write_state(state)

        self.state = state
        now = datetime.now(PARIS_TZ)
        for sess in sessions:
            dt = _hm_to_dt(sess["hm"], today)
            if sess.get("started") and not sess.get("ended"):
                end_iso = sess.get("end")
                if end_iso:
                    end_dt = datetime.fromisoformat(end_iso)
                    if end_dt > now:
                        self._resume_session(sess, (end_dt - now).total_seconds())
                    else:
                        await self._end_session(sess, announce=False)
            else:
                self._schedule_session(dt, sess)

    def _schedule_session(self, dt: datetime, session: Dict[str, Any]) -> None:
        """Planifier ``session`` pour démarrer à ``dt``."""
        end_dt = dt + timedelta(minutes=XP_DOUBLE_VOICE_DURATION_MINUTES)
        now = datetime.now(PARIS_TZ)
        if end_dt <= now:
            return
        delay = max(0, (dt - now).total_seconds())
        task = self.bot.loop.create_task(self._run_session(session, delay))
        self._tasks.append(task)

    def _resume_session(self, session: Dict[str, Any], delay: float) -> None:
        """Reprendre une session déjà démarrée et programmée pour se terminer."""
        set_voice_bonus(True)
        task = self.bot.loop.create_task(self._finish_session(session, delay))
        self._tasks.append(task)

    async def _finish_session(self, session: Dict[str, Any], delay: float) -> None:
        await asyncio.sleep(delay)
        await self._end_session(session)

    async def _run_session(self, session: Dict[str, Any], delay: float) -> None:
        """Attendre ``delay`` secondes puis exécuter ``session``."""
        await asyncio.sleep(delay)
        await self._start_session(session)
        await asyncio.sleep(XP_DOUBLE_VOICE_DURATION_MINUTES * 60)
        await self._end_session(session)

    async def _start_session(self, session: Dict[str, Any]) -> None:
        """Activer le bonus et annoncer le début de ``session``."""
        if session.get("started"):
            return
        session["started"] = True
        end_dt = datetime.now(PARIS_TZ) + timedelta(
            minutes=XP_DOUBLE_VOICE_DURATION_MINUTES
        )
        session["end"] = end_dt.isoformat()
        await _write_state(self.state)
        set_voice_bonus(True)
        logger.info(
            "[double_xp] session started at %s", end_dt.isoformat()
        )
        channel = self.bot.get_channel(XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID)
        if channel:
            try:
                await channel.send(
                    "Hey 🎉 À partir de maintenant, c’est DOUBLE XP en vocal ! Profitez-en 😉"
                )
            except Exception as e:  # pragma: no cover - network errors
                logger.warning("[double_xp] Failed to send start message: %s", e)

    async def _end_session(
        self, session: Dict[str, Any], announce: bool = True
    ) -> None:
        """Désactiver le bonus et annoncer la fin de ``session``."""
        if session.get("ended"):
            return
        session["ended"] = True
        await _write_state(self.state)
        set_voice_bonus(False)
        logger.info("[double_xp] session ended")
        channel = self.bot.get_channel(XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID)
        if announce and channel:
            try:
                await channel.send(
                    "✅ La session Double XP vocale est terminée pour aujourd’hui, merci à ceux qui étaient présents !"
                )
            except Exception as e:  # pragma: no cover - network errors
                logger.warning("[double_xp] Failed to send end message: %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DoubleVoiceXP(bot))
