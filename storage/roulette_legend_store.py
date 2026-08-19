from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.db import DB_PATH
from utils.timezones import PARIS_TZ


CASINO_LEGEND_WINDOW_HOURS = 24
CASINO_LEGEND_MAX_ROWS = 5000


@dataclass(frozen=True, slots=True)
class RouletteLegendEvidence:
    max_house_streak: int = 0
    max_user_net_xp: int = 0
    max_user_wins: int = 0
    max_user_number_wins: int = 0
    zero_count: int = 0
    max_payout_xp: int = 0
    black_night_qualified: bool = False
    ghost_player_qualified: bool = False


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RouletteLegendStore:
    """Read-only Lot 5 evidence projection over the existing roulette table."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _rows_sync(self, cutoff: str) -> list[sqlite3.Row]:
        if not self.path.is_file():
            return []
        try:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='roulette_events'"
                ).fetchone()
                if exists is None:
                    return []
                rows = connection.execute(
                    """
                    SELECT * FROM (
                        SELECT
                            id,
                            user_id,
                            bet_type,
                            wager_xp,
                            payout_xp,
                            won,
                            zero_hit,
                            occurred_at
                        FROM roulette_events
                        WHERE occurred_at >= ?
                        ORDER BY occurred_at DESC, id DESC
                        LIMIT ?
                    )
                    ORDER BY occurred_at ASC, id ASC
                    """,
                    (cutoff, CASINO_LEGEND_MAX_ROWS),
                ).fetchall()
        except sqlite3.Error:
            return []
        return list(rows)

    @staticmethod
    def _build_evidence(rows: list[sqlite3.Row]) -> RouletteLegendEvidence:
        if not rows:
            return RouletteLegendEvidence()

        user_stats: dict[int, dict[str, int]] = {}
        zero_count = 0
        max_payout = 0
        current_house_streak = 0
        max_house_streak = 0
        parsed_rows: list[tuple[sqlite3.Row, datetime]] = []
        nights: dict[str, dict[str, object]] = {}

        for row in rows:
            occurred = _parse_timestamp(row["occurred_at"])
            if occurred is None:
                continue
            parsed_rows.append((row, occurred))

            user_id = int(row["user_id"])
            wager = max(0, int(row["wager_xp"]))
            payout = max(0, int(row["payout_xp"]))
            won = bool(row["won"])
            zero_hit = bool(row["zero_hit"])
            bet_type = str(row["bet_type"])

            stats = user_stats.setdefault(
                user_id,
                {"net": 0, "wins": 0, "number_wins": 0},
            )
            stats["net"] += payout - wager
            if won:
                stats["wins"] += 1
                if bet_type == "number":
                    stats["number_wins"] += 1

            if zero_hit:
                zero_count += 1
            max_payout = max(max_payout, payout)

            if won:
                current_house_streak = 0
            else:
                current_house_streak += 1
                max_house_streak = max(max_house_streak, current_house_streak)

            local = occurred.astimezone(PARIS_TZ)
            if local.hour >= 22:
                night_key = local.date().isoformat()
            elif local.hour < 5:
                night_key = (local.date() - timedelta(days=1)).isoformat()
            else:
                night_key = ""
            if night_key:
                night = nights.setdefault(
                    night_key,
                    {"bets": 0, "house_net": 0, "users": set()},
                )
                night["bets"] = int(night["bets"]) + 1
                night["house_net"] = int(night["house_net"]) + wager - payout
                users = night["users"]
                if isinstance(users, set):
                    users.add(user_id)

        black_night = any(
            int(night["bets"]) >= 20
            and int(night["house_net"]) >= 1500
            and isinstance(night["users"], set)
            and len(night["users"]) >= 2
            for night in nights.values()
        )

        ghost_player = False
        for index, (row, occurred) in enumerate(parsed_rows):
            if not bool(row["won"]) or str(row["bet_type"]) != "number":
                continue
            local = occurred.astimezone(PARIS_TZ)
            if not 2 <= local.hour < 5:
                continue
            candidate_user = int(row["user_id"])
            window_start = occurred - timedelta(minutes=30)
            users: set[int] = set()
            for previous_row, previous_at in parsed_rows[: index + 1]:
                if previous_at < window_start or previous_at > occurred:
                    continue
                users.add(int(previous_row["user_id"]))
            if users == {candidate_user}:
                ghost_player = True
                break

        return RouletteLegendEvidence(
            max_house_streak=max_house_streak,
            max_user_net_xp=max(
                (stats["net"] for stats in user_stats.values()),
                default=0,
            ),
            max_user_wins=max(
                (stats["wins"] for stats in user_stats.values()),
                default=0,
            ),
            max_user_number_wins=max(
                (stats["number_wins"] for stats in user_stats.values()),
                default=0,
            ),
            zero_count=zero_count,
            max_payout_xp=max_payout,
            black_night_qualified=black_night,
            ghost_player_qualified=ghost_player,
        )

    async def get_evidence(
        self,
        *,
        at: datetime | None = None,
        window_hours: int = CASINO_LEGEND_WINDOW_HOURS,
    ) -> RouletteLegendEvidence:
        now = _aware_utc(at)
        cutoff = now - timedelta(hours=max(1, int(window_hours)))
        rows = await asyncio.to_thread(self._rows_sync, cutoff.isoformat())
        return await asyncio.to_thread(self._build_evidence, rows)


roulette_legend_store = RouletteLegendStore()


__all__ = [
    "CASINO_LEGEND_MAX_ROWS",
    "CASINO_LEGEND_WINDOW_HOURS",
    "RouletteLegendEvidence",
    "RouletteLegendStore",
    "roulette_legend_store",
]
