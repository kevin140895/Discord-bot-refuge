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
from typing import List

import discord
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

try:
    from zoneinfo import ZoneInfo

    PARIS_TZ = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - fallback when zoneinfo is missing
    from datetime import timezone

    PARIS_TZ = timezone.utc

STATE_FILE = os.path.join(DATA_DIR, "double_voice_xp.json")
ensure_dir(DATA_DIR)


def _read_state() -> dict:
    return read_json_safe(STATE_FILE)


async def _write_state(data: dict) -> None:
    await atomic_write_json_async(STATE_FILE, data)


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
    h, m = map(int, hm.split(":"))
    return datetime.combine(day, time(hour=h, minute=m, tzinfo=PARIS_TZ))


class DoubleVoiceXP(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tasks: List[asyncio.Task] = []
        self.daily_planner.start()
        self.bot.loop.create_task(self._startup())

    async def _startup(self) -> None:
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
        today = datetime.now(PARIS_TZ).date()
        state = _read_state()
        if force or state.get("date") != today.isoformat():
            sessions = _random_sessions()
            state = {"date": today.isoformat(), "sessions": sessions}
            await _write_state(state)
        sessions = state.get("sessions", [])
        for hm in sessions:
            dt = _hm_to_dt(hm, today)
            self._schedule_session(dt)

    def _schedule_session(self, dt: datetime) -> None:
        end_dt = dt + timedelta(minutes=XP_DOUBLE_VOICE_DURATION_MINUTES)
        now = datetime.now(PARIS_TZ)
        if end_dt <= now:
            return
        delay = max(0, (dt - now).total_seconds())
        task = self.bot.loop.create_task(self._run_session(delay))
        self._tasks.append(task)

    async def _run_session(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._start_session()
        await asyncio.sleep(XP_DOUBLE_VOICE_DURATION_MINUTES * 60)
        await self._end_session()

    async def _start_session(self) -> None:
        set_voice_bonus(True)
        channel = self.bot.get_channel(XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID)
        if channel:
            try:
                await channel.send(
                    "Hey 🎉 À partir de maintenant, c’est DOUBLE XP en vocal ! Profitez-en 😉"
                )
            except Exception as e:  # pragma: no cover - network errors
                logging.warning("[double_xp] Failed to send start message: %s", e)

    async def _end_session(self) -> None:
        set_voice_bonus(False)
        channel = self.bot.get_channel(XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID)
        if channel:
            try:
                await channel.send(
                    "✅ La session Double XP vocale est terminée pour aujourd’hui, merci à ceux qui étaient présents !"
                )
            except Exception as e:  # pragma: no cover - network errors
                logging.warning("[double_xp] Failed to send end message: %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DoubleVoiceXP(bot))
