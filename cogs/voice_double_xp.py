"""Gestion des sessions Double XP vocal.

Le multiplicateur ×2 sur l'XP vocal peut être déclenché manuellement via les
sessions stockées dans l'état persistant. Aucune planification aléatoire
n'est désormais générée automatiquement ; la liste des créneaux reste vide
tant qu'elle n'est pas remplie ailleurs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time, timedelta
from typing import List, Dict, Any

from discord.ext import commands, tasks

from config import (
    DATA_DIR,
    XP_DOUBLE_VOICE_DURATION_MINUTES,
    XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID,
)
from utils.persistence import read_json_safe, atomic_write_json_async, ensure_dir
from utils.voice_bonus import register_voice_bonus_window, set_voice_bonus
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


def _hm_to_dt(hm: str, day: date) -> datetime:
    """Convertit une heure ``HH:MM`` en :class:`datetime` pour ``day``."""
    h, m = map(int, hm.split(":"))
    return datetime.combine(day, time(hour=h, minute=m, tzinfo=PARIS_TZ))


def _session_start(session: Dict[str, Any], fallback: datetime) -> datetime:
    start_iso = session.get("start")
    if start_iso:
        try:
            return datetime.fromisoformat(start_iso)
        except (TypeError, ValueError):
            logger.warning("[double_xp] invalid persisted start: %r", start_iso)
    return fallback


class DoubleVoiceXP(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tasks: List[asyncio.Task] = []
        self.state: Dict[str, Any] = {}
        # Empêche les appels concurrents à ``_prepare_today``.
        self._prepare_lock = asyncio.Lock()
        self.daily_planner.start()
        asyncio.create_task(self._startup())

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
        try:
            await self._prepare_today(force=True)
        except Exception:
            logger.exception("Erreur dans daily_planner")

    @daily_planner.before_loop
    async def before_daily_planner(self) -> None:  # pragma: no cover - simple wait
        await self.bot.wait_until_ready()

    async def _prepare_today(self, force: bool = False) -> None:
        """Lire/initialiser l'état du jour puis planifier ou reprendre les sessions."""

        async with self._prepare_lock:
            # Cancel any previously scheduled tasks to avoid duplicates.
            for task in self._tasks:
                task.cancel()
            self._tasks.clear()

            today = datetime.now(PARIS_TZ).date()
            state = _read_state()
            if force or state.get("date") != today.isoformat():
                sessions: List[Dict[str, Any]] = []
                state = {"date": today.isoformat(), "sessions": sessions}
                await _write_state(state)
            else:
                sessions = state.get("sessions", [])
                if sessions and isinstance(sessions[0], str):  # rétro-compatibilité
                    sessions = [
                        {
                            "hm": hm,
                            "start": None,
                            "started": False,
                            "end": None,
                            "ended": False,
                        }
                        for hm in sessions
                    ]
                    state["sessions"] = sessions
                    await _write_state(state)

            self.state = state
            now = datetime.now(PARIS_TZ)
            for sess in sessions:
                scheduled_start = _hm_to_dt(sess["hm"], today)
                if sess.get("started"):
                    start_dt = _session_start(sess, scheduled_start)
                    end_iso = sess.get("end")
                    if not end_iso:
                        continue
                    try:
                        end_dt = datetime.fromisoformat(end_iso)
                    except (TypeError, ValueError):
                        logger.warning("[double_xp] invalid persisted end: %r", end_iso)
                        continue

                    if sess.get("ended"):
                        register_voice_bonus_window(start_dt, end_dt)
                    elif end_dt > now:
                        self._resume_session(
                            sess,
                            (end_dt - now).total_seconds(),
                            start_dt,
                        )
                    else:
                        # The bot was offline when the bonus should have ended.
                        # Restore only the real persisted interval, never the
                        # downtime between the scheduled end and this restart.
                        register_voice_bonus_window(start_dt, end_dt)
                        await self._end_session(
                            sess,
                            announce=False,
                            ended_at=end_dt,
                        )
                else:
                    self._schedule_session(scheduled_start, sess)

    def _schedule_session(self, dt: datetime, session: Dict[str, Any]) -> None:
        """Planifier ``session`` pour démarrer à ``dt``."""
        end_dt = dt + timedelta(minutes=XP_DOUBLE_VOICE_DURATION_MINUTES)
        now = datetime.now(PARIS_TZ)
        if end_dt <= now:
            return
        delay = max(0, (dt - now).total_seconds())
        task = asyncio.create_task(self._run_session(session, delay))
        self._tasks.append(task)

    def _resume_session(
        self,
        session: Dict[str, Any],
        delay: float,
        start_dt: datetime | None = None,
    ) -> None:
        """Reprendre une session déjà démarrée et programmée pour se terminer."""
        set_voice_bonus(True, at=start_dt)
        task = asyncio.create_task(self._finish_session(session, delay))
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
        start_dt = datetime.now(PARIS_TZ)
        end_dt = start_dt + timedelta(minutes=XP_DOUBLE_VOICE_DURATION_MINUTES)
        session["started"] = True
        session["start"] = start_dt.isoformat()
        session["end"] = end_dt.isoformat()
        await _write_state(self.state)
        set_voice_bonus(True, at=start_dt)
        logger.info("[double_xp] session started at %s", end_dt.isoformat())
        channel = self.bot.get_channel(XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID)
        if channel:
            try:
                await channel.send(
                    "Hey 🎉 À partir de maintenant, c’est DOUBLE XP en vocal ! Profitez-en 😉"
                )
            except Exception as e:  # pragma: no cover - network errors
                logger.warning("[double_xp] Failed to send start message: %s", e)

    async def _end_session(
        self,
        session: Dict[str, Any],
        announce: bool = True,
        ended_at: datetime | None = None,
    ) -> None:
        """Désactiver le bonus et annoncer la fin de ``session``."""
        if session.get("ended"):
            return
        effective_end = ended_at or datetime.now(PARIS_TZ)
        session["ended"] = True
        session["end"] = effective_end.isoformat()
        await _write_state(self.state)
        set_voice_bonus(False, at=effective_end)
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
