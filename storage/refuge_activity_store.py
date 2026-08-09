from __future__ import annotations

import asyncio
import weakref
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe
from utils.seasons import split_interval_by_season


REFUGE_ACTIVITY_SCHEMA_VERSION = 1
REFUGE_ACTIVITY_FILE = Path(DATA_DIR) / "refuge_activity.json"


class RefugeActivitySchemaError(ValueError):
    """Raised when persisted Refuge activity uses an unsupported schema."""


def _utc_iso(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


class RefugeActivityStore:
    """Persist derived Refuge activity without changing XP or season metrics."""

    def __init__(self, path: str | Path = REFUGE_ACTIVITY_FILE) -> None:
        self.path = Path(path)
        self._loaded = False
        self._dirty = False
        self._data: dict[str, Any] = self._empty_data()
        self._locks: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {
            "schema_version": REFUGE_ACTIVITY_SCHEMA_VERSION,
            "tracking_started_at": None,
            "community_voice_seconds": 0,
            "seasons": {},
        }

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

        raw = await asyncio.to_thread(read_json_safe, self.path, None)
        if raw is None:
            self._data = self._empty_data()
            self._loaded = True
            return
        if not isinstance(raw, Mapping):
            self._data = self._empty_data()
            self._loaded = True
            return

        try:
            version = int(raw.get("schema_version", 0))
        except (TypeError, ValueError):
            version = 0
        if version != REFUGE_ACTIVITY_SCHEMA_VERSION:
            raise RefugeActivitySchemaError(
                "unsupported refuge activity schema "
                f"{version}; expected {REFUGE_ACTIVITY_SCHEMA_VERSION}"
            )

        try:
            total_seconds = max(0, int(raw.get("community_voice_seconds", 0)))
        except (TypeError, ValueError):
            total_seconds = 0

        seasons: dict[str, dict[str, int]] = {}
        raw_seasons = raw.get("seasons", {})
        if isinstance(raw_seasons, Mapping):
            for raw_season_id, raw_payload in raw_seasons.items():
                if not isinstance(raw_payload, Mapping):
                    continue
                try:
                    seconds = max(
                        0,
                        int(raw_payload.get("community_voice_seconds", 0)),
                    )
                except (TypeError, ValueError):
                    seconds = 0
                seasons[str(raw_season_id)] = {
                    "community_voice_seconds": seconds,
                }

        tracking_started_at = raw.get("tracking_started_at")
        self._data = {
            "schema_version": REFUGE_ACTIVITY_SCHEMA_VERSION,
            "tracking_started_at": (
                str(tracking_started_at) if tracking_started_at else None
            ),
            "community_voice_seconds": total_seconds,
            "seasons": seasons,
        }
        self._loaded = True

    async def initialize(
        self,
        *,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        async with self._get_lock():
            await self._load_locked()
            if not self._data.get("tracking_started_at"):
                self._data["tracking_started_at"] = _utc_iso(at)
                await atomic_write_json_async(self.path, self._data)
            return deepcopy(self._data)

    async def record_interval(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> int:
        parts = split_interval_by_season(started_at, ended_at)
        recorded_seconds = sum(seconds for _season_id, seconds in parts)
        if recorded_seconds <= 0:
            return 0

        async with self._get_lock():
            await self._load_locked()
            if not self._data.get("tracking_started_at"):
                self._data["tracking_started_at"] = _utc_iso(started_at)
            self._data["community_voice_seconds"] = (
                int(self._data.get("community_voice_seconds", 0))
                + recorded_seconds
            )
            seasons = self._data.setdefault("seasons", {})
            for season_id, seconds in parts:
                season = seasons.setdefault(
                    season_id,
                    {"community_voice_seconds": 0},
                )
                season["community_voice_seconds"] = (
                    int(season.get("community_voice_seconds", 0))
                    + seconds
                )
            self._dirty = True
        return recorded_seconds

    async def get_snapshot(self) -> dict[str, Any]:
        async with self._get_lock():
            await self._load_locked()
            return deepcopy(self._data)

    async def get_total_seconds(self) -> int:
        snapshot = await self.get_snapshot()
        return int(snapshot.get("community_voice_seconds", 0))

    async def get_season_seconds(self, season_id: str) -> int:
        snapshot = await self.get_snapshot()
        season = snapshot.get("seasons", {}).get(season_id, {})
        if not isinstance(season, Mapping):
            return 0
        try:
            return max(0, int(season.get("community_voice_seconds", 0)))
        except (TypeError, ValueError):
            return 0

    async def flush(self) -> None:
        async with self._get_lock():
            await self._load_locked()
            if not self._dirty:
                return
            await atomic_write_json_async(self.path, self._data)
            self._dirty = False


refuge_activity_store = RefugeActivityStore()


__all__ = [
    "REFUGE_ACTIVITY_FILE",
    "REFUGE_ACTIVITY_SCHEMA_VERSION",
    "RefugeActivitySchemaError",
    "RefugeActivityStore",
    "refuge_activity_store",
]
