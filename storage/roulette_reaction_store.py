from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

from storage.db import DB_PATH


CASINO_ACTIVITY_WINDOW_MINUTES: Final[int] = 10
CASINO_ZERO_REACTION_MINUTES: Final[int] = 5
CASINO_BIG_WIN_REACTION_MINUTES: Final[int] = 10
CASINO_STRONG_STREAK_MAX_IDLE_MINUTES: Final[int] = 15
CASINO_BIG_WIN_MIN_PAYOUT_XP: Final[int] = 1000
CASINO_STRONG_STREAK_MIN: Final[int] = 5
CASINO_ACTIVE_BETS_MIN: Final[int] = 3
CASINO_BUSY_BETS_MIN: Final[int] = 8
CASINO_BUSY_PLAYERS_MIN: Final[int] = 4


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _empty_snapshot() -> dict[str, Any]:
    return {
        "bets_10m": 0,
        "unique_players_10m": 0,
        "latest_event_at": None,
        "latest_zero_at": None,
        "latest_big_win_at": None,
        "latest_big_win_payout_xp": 0,
        "streak_side": None,
        "streak_count": 0,
        "streak_at": None,
    }


class RouletteReactionStore:
    """Read-only projection of roulette_events for short-lived visual reactions."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _snapshot_sync(self, now: datetime) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_snapshot()

        activity_cutoff = now - timedelta(minutes=CASINO_ACTIVITY_WINDOW_MINUTES)
        zero_cutoff = now - timedelta(minutes=CASINO_ZERO_REACTION_MINUTES)
        big_win_cutoff = now - timedelta(minutes=CASINO_BIG_WIN_REACTION_MINUTES)

        try:
            with self._connect() as connection:
                activity = connection.execute(
                    """
                    SELECT COUNT(*) AS bets, COUNT(DISTINCT user_id) AS players
                    FROM roulette_events
                    WHERE occurred_at >= ?
                    """,
                    (activity_cutoff.isoformat(),),
                ).fetchone()
                latest = connection.execute(
                    """
                    SELECT occurred_at
                    FROM roulette_events
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
                zero = connection.execute(
                    """
                    SELECT occurred_at
                    FROM roulette_events
                    WHERE zero_hit = 1 AND occurred_at >= ?
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT 1
                    """,
                    (zero_cutoff.isoformat(),),
                ).fetchone()
                big_win = connection.execute(
                    """
                    SELECT occurred_at, payout_xp
                    FROM roulette_events
                    WHERE won = 1 AND payout_xp >= ? AND occurred_at >= ?
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT 1
                    """,
                    (CASINO_BIG_WIN_MIN_PAYOUT_XP, big_win_cutoff.isoformat()),
                ).fetchone()
                streak_rows = connection.execute(
                    """
                    SELECT won, occurred_at
                    FROM roulette_events
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT 32
                    """
                ).fetchall()
        except sqlite3.OperationalError as exc:
            # During a clean boot the Lot 2 table can legitimately not exist yet.
            if "no such table" in str(exc).lower():
                return _empty_snapshot()
            raise

        streak_side: str | None = None
        streak_count = 0
        streak_at: str | None = None
        if streak_rows:
            latest_won = bool(streak_rows[0]["won"])
            streak_at = str(streak_rows[0]["occurred_at"])
            for row in streak_rows:
                if bool(row["won"]) != latest_won:
                    break
                streak_count += 1
            streak_side = "players" if latest_won else "house"

        return {
            "bets_10m": int(activity["bets"] or 0) if activity is not None else 0,
            "unique_players_10m": (
                int(activity["players"] or 0) if activity is not None else 0
            ),
            "latest_event_at": str(latest["occurred_at"]) if latest is not None else None,
            "latest_zero_at": str(zero["occurred_at"]) if zero is not None else None,
            "latest_big_win_at": (
                str(big_win["occurred_at"]) if big_win is not None else None
            ),
            "latest_big_win_payout_xp": (
                int(big_win["payout_xp"] or 0) if big_win is not None else 0
            ),
            "streak_side": streak_side,
            "streak_count": streak_count,
            "streak_at": streak_at,
        }

    async def get_snapshot(self, *, at: datetime | None = None) -> dict[str, Any]:
        now = _aware_utc(at)
        return await asyncio.to_thread(self._snapshot_sync, now)


roulette_reaction_store = RouletteReactionStore()


__all__ = [
    "CASINO_ACTIVE_BETS_MIN",
    "CASINO_ACTIVITY_WINDOW_MINUTES",
    "CASINO_BIG_WIN_MIN_PAYOUT_XP",
    "CASINO_BIG_WIN_REACTION_MINUTES",
    "CASINO_BUSY_BETS_MIN",
    "CASINO_BUSY_PLAYERS_MIN",
    "CASINO_STRONG_STREAK_MAX_IDLE_MINUTES",
    "CASINO_STRONG_STREAK_MIN",
    "CASINO_ZERO_REACTION_MINUTES",
    "RouletteReactionStore",
    "roulette_reaction_store",
]
