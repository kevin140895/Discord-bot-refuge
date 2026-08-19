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
SIMPLE_BET_TYPES = frozenset({"red", "black", "even", "odd"})

GRAND_HEIST_MIN_NET_XP = 8000
GRAND_HEIST_MIN_WINS = 8
GRAND_HEIST_MIN_BETS = 15
BLACK_NIGHT_MIN_BETS = 30
BLACK_NIGHT_MIN_HOUSE_NET_XP = 4000
BLACK_NIGHT_MIN_PLAYERS = 3
HOUSE_LEGEND_MIN_STREAK = 15
BREAK_IN_CONSECUTIVE_NUMBER_WINS = 2

# Secret thresholds remain deliberately absent from the public Discord UI.
BLACK_CAT_WINDOW_SPINS = 12
BLACK_CAT_ZEROES_REQUIRED = 3
DIAMOND_MIN_WAGER_XP = 500
DIAMOND_MIN_PAYOUT_XP = 5000
GHOST_WINDOW_MINUTES = 90
GHOST_NUMBER_WINS_REQUIRED = 2


@dataclass(frozen=True, slots=True)
class RouletteLegendEvidence:
    max_house_streak: int = 0
    zero_count: int = 0
    max_payout_xp: int = 0
    grand_heist_qualified: bool = False
    break_in_qualified: bool = False
    black_night_qualified: bool = False
    black_cat_qualified: bool = False
    diamond_qualified: bool = False
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


def _is_ghost_hour(occurred: datetime) -> bool:
    local = occurred.astimezone(PARIS_TZ)
    return 2 <= local.hour < 5


class RouletteLegendStore:
    """Read-only V2 legend evidence projection over existing roulette history."""

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
                {"net": 0, "wins": 0, "bets": 0},
            )
            stats["bets"] += 1
            stats["net"] += payout - wager
            if won:
                stats["wins"] += 1

            if zero_hit:
                zero_count += 1
            max_payout = max(max_payout, payout)

            # V2 deliberately ignores number bets for the House streak.
            if bet_type in SIMPLE_BET_TYPES:
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

        grand_heist = any(
            stats["net"] >= GRAND_HEIST_MIN_NET_XP
            and stats["wins"] >= GRAND_HEIST_MIN_WINS
            and stats["bets"] >= GRAND_HEIST_MIN_BETS
            for stats in user_stats.values()
        )

        black_night = any(
            int(night["bets"]) >= BLACK_NIGHT_MIN_BETS
            and int(night["house_net"]) >= BLACK_NIGHT_MIN_HOUSE_NET_XP
            and isinstance(night["users"], set)
            and len(night["users"]) >= BLACK_NIGHT_MIN_PLAYERS
            for night in nights.values()
        )

        break_in = False
        for (previous, _previous_at), (current, _current_at) in zip(
            parsed_rows,
            parsed_rows[1:],
        ):
            if (
                int(previous["user_id"]) == int(current["user_id"])
                and str(previous["bet_type"]) == "number"
                and str(current["bet_type"]) == "number"
                and bool(previous["won"])
                and bool(current["won"])
            ):
                break_in = True
                break

        black_cat = False
        zero_flags = [bool(row["zero_hit"]) for row, _occurred in parsed_rows]
        for index in range(len(zero_flags)):
            start = max(0, index - BLACK_CAT_WINDOW_SPINS + 1)
            if sum(zero_flags[start : index + 1]) >= BLACK_CAT_ZEROES_REQUIRED:
                black_cat = True
                break

        diamond = any(
            str(row["bet_type"]) == "number"
            and bool(row["won"])
            and int(row["wager_xp"]) >= DIAMOND_MIN_WAGER_XP
            and int(row["payout_xp"]) >= DIAMOND_MIN_PAYOUT_XP
            for row, _occurred in parsed_rows
        )

        ghost_player = False
        ghost_window = timedelta(minutes=GHOST_WINDOW_MINUTES)
        for row, occurred in parsed_rows:
            if (
                str(row["bet_type"]) != "number"
                or not bool(row["won"])
                or not _is_ghost_hour(occurred)
            ):
                continue
            candidate_user = int(row["user_id"])
            window_start = occurred - ghost_window
            window_rows = [
                (candidate, candidate_at)
                for candidate, candidate_at in parsed_rows
                if window_start <= candidate_at <= occurred
            ]
            users = {int(candidate["user_id"]) for candidate, _at in window_rows}
            if users != {candidate_user}:
                continue
            qualifying_wins = sum(
                1
                for candidate, candidate_at in window_rows
                if int(candidate["user_id"]) == candidate_user
                and str(candidate["bet_type"]) == "number"
                and bool(candidate["won"])
                and _is_ghost_hour(candidate_at)
            )
            if qualifying_wins >= GHOST_NUMBER_WINS_REQUIRED:
                ghost_player = True
                break

        return RouletteLegendEvidence(
            max_house_streak=max_house_streak,
            zero_count=zero_count,
            max_payout_xp=max_payout,
            grand_heist_qualified=grand_heist,
            break_in_qualified=break_in,
            black_night_qualified=black_night,
            black_cat_qualified=black_cat,
            diamond_qualified=diamond,
            ghost_player_qualified=ghost_player,
        )

    async def get_evidence(
        self,
        *,
        at: datetime | None = None,
        window_hours: int = CASINO_LEGEND_WINDOW_HOURS,
        since: datetime | str | None = None,
    ) -> RouletteLegendEvidence:
        now = _aware_utc(at)
        cutoff = now - timedelta(hours=max(1, int(window_hours)))
        since_at: datetime | None
        if isinstance(since, datetime):
            since_at = _aware_utc(since)
        elif since is not None:
            since_at = _parse_timestamp(since)
        else:
            since_at = None
        if since_at is not None and since_at > cutoff:
            cutoff = since_at
        rows = await asyncio.to_thread(self._rows_sync, cutoff.isoformat())
        return await asyncio.to_thread(self._build_evidence, rows)


roulette_legend_store = RouletteLegendStore()


__all__ = [
    "BLACK_NIGHT_MIN_BETS",
    "BLACK_NIGHT_MIN_HOUSE_NET_XP",
    "BLACK_NIGHT_MIN_PLAYERS",
    "BREAK_IN_CONSECUTIVE_NUMBER_WINS",
    "CASINO_LEGEND_MAX_ROWS",
    "CASINO_LEGEND_WINDOW_HOURS",
    "GRAND_HEIST_MIN_BETS",
    "GRAND_HEIST_MIN_NET_XP",
    "GRAND_HEIST_MIN_WINS",
    "HOUSE_LEGEND_MIN_STREAK",
    "RouletteLegendEvidence",
    "RouletteLegendStore",
    "roulette_legend_store",
]
