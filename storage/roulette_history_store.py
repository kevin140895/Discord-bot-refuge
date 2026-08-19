from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from storage.db import DB_PATH


ROULETTE_HISTORY_RETENTION_DAYS = 30
ROULETTE_SPOTLIGHT_WINDOW_HOURS = 24
VALID_BET_TYPES = frozenset({"red", "black", "even", "odd", "number"})


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class RouletteHistoryStore:
    """SQLite-backed recent roulette history used by the living casino panel."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS roulette_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    bet_type TEXT NOT NULL,
                    wager_xp INTEGER NOT NULL CHECK (wager_xp >= 0),
                    payout_xp INTEGER NOT NULL CHECK (payout_xp >= 0),
                    won INTEGER NOT NULL CHECK (won IN (0, 1)),
                    zero_hit INTEGER NOT NULL CHECK (zero_hit IN (0, 1)),
                    selected_number INTEGER,
                    drawn_number INTEGER,
                    occurred_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_roulette_events_occurred_at
                    ON roulette_events (occurred_at DESC, id DESC);

                CREATE INDEX IF NOT EXISTS idx_roulette_events_user_time
                    ON roulette_events (user_id, occurred_at DESC);
                """
            )
            connection.commit()

    async def start(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    @staticmethod
    def _validate_number(value: int | None, *, field: str) -> int | None:
        if value is None:
            return None
        number = int(value)
        if not 0 <= number <= 36:
            raise ValueError(f"{field} must be between 0 and 36")
        return number

    def _record_sync(
        self,
        user_id: int,
        bet_type: str,
        wager_xp: int,
        payout_xp: int,
        won: bool,
        zero_hit: bool,
        selected_number: int | None,
        drawn_number: int | None,
        occurred_at: str,
        retention_cutoff: str,
    ) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO roulette_events (
                    user_id,
                    bet_type,
                    wager_xp,
                    payout_xp,
                    won,
                    zero_hit,
                    selected_number,
                    drawn_number,
                    occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    bet_type,
                    wager_xp,
                    payout_xp,
                    1 if won else 0,
                    1 if zero_hit else 0,
                    selected_number,
                    drawn_number,
                    occurred_at,
                ),
            )
            connection.execute(
                "DELETE FROM roulette_events WHERE occurred_at < ?",
                (retention_cutoff,),
            )
            connection.commit()
            return int(cursor.lastrowid)

    async def record_event(
        self,
        *,
        user_id: int,
        bet_type: str,
        wager_xp: int,
        payout_xp: int,
        won: bool,
        zero_hit: bool,
        selected_number: int | None = None,
        drawn_number: int | None = None,
        at: datetime | None = None,
    ) -> int:
        normalized_type = str(bet_type).strip().lower()
        if normalized_type not in VALID_BET_TYPES:
            raise ValueError(f"unsupported roulette bet type: {bet_type!r}")
        wager = int(wager_xp)
        payout = int(payout_xp)
        if wager < 0 or payout < 0:
            raise ValueError("roulette XP amounts must be non-negative")
        selected = self._validate_number(selected_number, field="selected_number")
        drawn = self._validate_number(drawn_number, field="drawn_number")
        if normalized_type == "number" and selected is None:
            raise ValueError("number bets require selected_number")
        if bool(zero_hit) and drawn not in {None, 0}:
            raise ValueError("zero_hit cannot have a non-zero drawn_number")

        now = _aware_utc(at)
        cutoff = now - timedelta(days=ROULETTE_HISTORY_RETENTION_DAYS)
        await self.start()
        async with self._lock:
            return await asyncio.to_thread(
                self._record_sync,
                int(user_id),
                normalized_type,
                wager,
                payout,
                bool(won),
                bool(zero_hit),
                selected,
                drawn,
                now.isoformat(),
                cutoff.isoformat(),
            )

    @staticmethod
    def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "bet_type": str(row["bet_type"]),
            "wager_xp": int(row["wager_xp"]),
            "payout_xp": int(row["payout_xp"]),
            "won": bool(row["won"]),
            "zero_hit": bool(row["zero_hit"]),
            "selected_number": (
                int(row["selected_number"])
                if row["selected_number"] is not None
                else None
            ),
            "drawn_number": (
                int(row["drawn_number"])
                if row["drawn_number"] is not None
                else None
            ),
            "occurred_at": str(row["occurred_at"]),
        }

    def _snapshot_sync(
        self,
        recent_limit: int,
        cutoff: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            recent_rows = connection.execute(
                """
                SELECT *
                FROM roulette_events
                ORDER BY occurred_at DESC, id DESC
                LIMIT ?
                """,
                (recent_limit,),
            ).fetchall()

            spotlight_row = connection.execute(
                """
                SELECT
                    user_id,
                    COUNT(*) AS bets,
                    SUM(won) AS wins,
                    SUM(wager_xp) AS wagered_xp,
                    SUM(payout_xp) AS payout_xp,
                    SUM(payout_xp - wager_xp) AS net_xp
                FROM roulette_events
                WHERE occurred_at >= ?
                GROUP BY user_id
                ORDER BY net_xp DESC, payout_xp DESC, wins DESC, bets ASC
                LIMIT 1
                """,
                (cutoff,),
            ).fetchone()

            biggest_row = connection.execute(
                """
                SELECT *
                FROM roulette_events
                WHERE occurred_at >= ? AND won = 1 AND payout_xp > 0
                ORDER BY payout_xp DESC, occurred_at DESC, id DESC
                LIMIT 1
                """,
                (cutoff,),
            ).fetchone()

            streak_rows = connection.execute(
                """
                SELECT won
                FROM roulette_events
                ORDER BY occurred_at DESC, id DESC
                LIMIT 32
                """
            ).fetchall()

        spotlight: dict[str, Any] | None = None
        if spotlight_row is not None and int(spotlight_row["net_xp"] or 0) > 0:
            spotlight = {
                "user_id": int(spotlight_row["user_id"]),
                "bets": int(spotlight_row["bets"]),
                "wins": int(spotlight_row["wins"] or 0),
                "wagered_xp": int(spotlight_row["wagered_xp"] or 0),
                "payout_xp": int(spotlight_row["payout_xp"] or 0),
                "net_xp": int(spotlight_row["net_xp"] or 0),
            }

        streak: dict[str, Any] | None = None
        if streak_rows:
            latest_won = bool(streak_rows[0]["won"])
            count = 0
            for row in streak_rows:
                if bool(row["won"]) != latest_won:
                    break
                count += 1
            if count >= 3:
                streak = {
                    "side": "players" if latest_won else "house",
                    "count": count,
                }

        return {
            "recent": [self._event_payload(row) for row in recent_rows],
            "spotlight": spotlight,
            "biggest_win": (
                self._event_payload(biggest_row) if biggest_row is not None else None
            ),
            "streak": streak,
        }

    async def get_living_snapshot(
        self,
        *,
        recent_limit: int = 6,
        window_hours: int = ROULETTE_SPOTLIGHT_WINDOW_HOURS,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        now = _aware_utc(at)
        cutoff = now - timedelta(hours=max(1, int(window_hours)))
        limit = max(1, min(12, int(recent_limit)))
        await self.start()
        return await asyncio.to_thread(
            self._snapshot_sync,
            limit,
            cutoff.isoformat(),
        )


roulette_history_store = RouletteHistoryStore()


__all__ = [
    "ROULETTE_HISTORY_RETENTION_DAYS",
    "ROULETTE_SPOTLIGHT_WINDOW_HOURS",
    "RouletteHistoryStore",
    "roulette_history_store",
]
