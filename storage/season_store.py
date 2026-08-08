from __future__ import annotations

import asyncio
import weakref
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe
from utils.seasons import SEASON_FIELDS, season_id_for, season_label


SEASON_STATS_FILE = Path(DATA_DIR) / "season_stats.json"


class SeasonStore:
    """In-memory seasonal counters with periodic atomic persistence."""

    def __init__(self, path: str | Path = SEASON_STATS_FILE) -> None:
        self.path = Path(path)
        self._loaded = False
        self._dirty = False
        self._data: dict[str, Any] = {
            "schema_version": 1,
            "tracking_started_at": None,
            "casino_baseline_initialized": False,
            "casino_baseline": {},
            "seasons": {},
        }
        self._locks: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    async def _load_locked(self) -> None:
        if self._loaded:
            return
        raw = await asyncio.to_thread(read_json_safe, self.path, {})
        seasons: dict[str, Any] = {}
        casino_baseline: dict[str, Any] = {}
        casino_baseline_initialized = False
        tracking_started_at = None
        if isinstance(raw, dict):
            raw_seasons = raw.get("seasons", {})
            if isinstance(raw_seasons, dict):
                seasons = {
                    str(season_id): dict(payload)
                    for season_id, payload in raw_seasons.items()
                    if isinstance(payload, dict)
                }
            raw_baseline = raw.get("casino_baseline", {})
            if isinstance(raw_baseline, dict):
                casino_baseline = {
                    str(user_id): dict(payload)
                    for user_id, payload in raw_baseline.items()
                    if isinstance(payload, dict)
                }
            casino_baseline_initialized = bool(
                raw.get("casino_baseline_initialized", False)
            )
            value = raw.get("tracking_started_at")
            if value:
                tracking_started_at = str(value)
        self._data = {
            "schema_version": 1,
            "tracking_started_at": tracking_started_at,
            "casino_baseline_initialized": casino_baseline_initialized,
            "casino_baseline": casino_baseline,
            "seasons": seasons,
        }
        self._loaded = True

    async def load(self) -> None:
        async with self._get_lock():
            await self._load_locked()

    async def ensure_tracking_started(self) -> str:
        """Set the first deployment timestamp once and keep it across seasons."""

        async with self._get_lock():
            await self._load_locked()
            current = self._data.get("tracking_started_at")
            if current:
                return str(current)
            current = datetime.now(timezone.utc).isoformat()
            self._data["tracking_started_at"] = current
            self._dirty = True
            await atomic_write_json_async(self.path, self._data)
            self._dirty = False
            return current

    def _apply_increments_locked(
        self,
        user_id: int,
        season_id: str,
        timestamp: str,
        increments: Mapping[str, int],
    ) -> None:
        if not self._data.get("tracking_started_at"):
            self._data["tracking_started_at"] = timestamp

        seasons = self._data["seasons"]
        season = seasons.setdefault(
            season_id,
            {
                "label": season_label(season_id),
                "started_at": timestamp,
                "users": {},
            },
        )
        users = season.setdefault("users", {})
        payload = users.setdefault(str(user_id), {})
        for field, value in increments.items():
            payload[field] = int(payload.get(field, 0)) + int(value)
        self._dirty = True

    async def record(
        self,
        user_id: int,
        *,
        season_id: str | None = None,
        at: datetime | None = None,
        **increments: int,
    ) -> None:
        """Add one or more metric deltas to a season without immediate disk I/O."""

        normalized: dict[str, int] = {}
        for field, raw_value in increments.items():
            if field not in SEASON_FIELDS:
                raise ValueError(f"unsupported season metric: {field}")
            value = int(raw_value)
            if value:
                normalized[field] = value
        if not normalized:
            return

        resolved_season_id = season_id or season_id_for(at)
        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        timestamp = now.astimezone(timezone.utc).isoformat()

        async with self._get_lock():
            await self._load_locked()
            self._apply_increments_locked(
                user_id,
                resolved_season_id,
                timestamp,
                normalized,
            )

    async def sync_casino_totals(
        self,
        players: Mapping[str, Mapping[str, Any]],
        *,
        at: datetime | None = None,
    ) -> None:
        """Convert cumulative casino totals into prospective seasonal deltas.

        Exactly one global snapshot is used as the deployment baseline. After
        that, a player appearing for the first time is treated as genuinely new
        activity and their cumulative counters are diffed from zero.
        """

        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        timestamp = now.astimezone(timezone.utc).isoformat()
        resolved_season_id = season_id_for(now)

        async with self._get_lock():
            await self._load_locked()
            baseline = self._data.setdefault("casino_baseline", {})
            initializing = not bool(
                self._data.get("casino_baseline_initialized", False)
            )

            for raw_user_id, payload in players.items():
                if not isinstance(payload, Mapping):
                    continue
                try:
                    user_id = int(raw_user_id)
                    current = {
                        "bets": max(0, int(payload.get("bets", 0))),
                        "wagered": max(0, int(payload.get("wagered", 0))),
                        "winnings": max(0, int(payload.get("winnings", 0))),
                    }
                except (TypeError, ValueError):
                    continue

                previous = baseline.get(str(user_id))
                baseline[str(user_id)] = current
                if initializing:
                    self._dirty = True
                    continue
                if not isinstance(previous, dict):
                    previous = {"bets": 0, "wagered": 0, "winnings": 0}

                try:
                    delta_bets = current["bets"] - int(previous.get("bets", 0))
                    delta_wagered = current["wagered"] - int(
                        previous.get("wagered", 0)
                    )
                    delta_winnings = current["winnings"] - int(
                        previous.get("winnings", 0)
                    )
                except (TypeError, ValueError):
                    self._dirty = True
                    continue

                # A reset/corruption of the source should reset the baseline,
                # never fabricate negative seasonal activity.
                if delta_bets < 0 or delta_wagered < 0 or delta_winnings < 0:
                    self._dirty = True
                    continue
                if delta_bets == 0 and delta_wagered == 0 and delta_winnings == 0:
                    continue

                self._apply_increments_locked(
                    user_id,
                    resolved_season_id,
                    timestamp,
                    {
                        "casino_bets": delta_bets,
                        "casino_net": delta_winnings - delta_wagered,
                    },
                )

            if initializing:
                self._data["casino_baseline_initialized"] = True
                self._dirty = True

    async def get_season(self, season_id: str) -> dict[str, Any] | None:
        async with self._get_lock():
            await self._load_locked()
            payload = self._data["seasons"].get(season_id)
            return deepcopy(payload) if isinstance(payload, dict) else None

    async def list_seasons(self) -> list[str]:
        async with self._get_lock():
            await self._load_locked()
            return sorted(self._data["seasons"].keys(), reverse=True)

    async def tracking_started_at(self) -> str | None:
        async with self._get_lock():
            await self._load_locked()
            value = self._data.get("tracking_started_at")
            return str(value) if value else None

    async def flush(self) -> None:
        """Atomically persist the latest snapshot only when counters changed."""

        async with self._get_lock():
            await self._load_locked()
            if not self._dirty:
                return
            await atomic_write_json_async(self.path, self._data)
            self._dirty = False


season_store = SeasonStore()


__all__ = ["SEASON_STATS_FILE", "SeasonStore", "season_store"]
