from __future__ import annotations

import asyncio
import weakref
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe
from utils.seasons import split_interval_by_season


REFUGE_ACTIVITY_SCHEMA_VERSION = 2
REFUGE_ACTIVITY_FILE = Path(DATA_DIR) / "refuge_activity.json"
RECENT_BUCKET_SECONDS = 60
RECENT_RETENTION_SECONDS = 48 * 60 * 60


class RefugeActivitySchemaError(ValueError):
    """Raised when persisted Refuge activity uses an unsupported schema."""


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _utc_iso(at: datetime | None = None) -> str:
    return _aware_utc(at).isoformat()


def _bucket_start(at: datetime) -> datetime:
    current = _aware_utc(at)
    return current.replace(second=0, microsecond=0)


def _split_recent_buckets(
    started_at: datetime,
    recorded_seconds: int,
) -> tuple[tuple[str, int], ...]:
    remaining = max(0, int(recorded_seconds))
    if remaining <= 0:
        return ()

    current = _aware_utc(started_at)
    parts: list[tuple[str, int]] = []
    while remaining > 0:
        bucket = _bucket_start(current)
        next_bucket = bucket + timedelta(seconds=RECENT_BUCKET_SECONDS)
        room = max(1, int((next_bucket - current).total_seconds()))
        seconds = min(remaining, room)
        parts.append((bucket.isoformat(), seconds))
        current += timedelta(seconds=seconds)
        remaining -= seconds
    return tuple(parts)


def _parse_bucket_key(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _bucket_start(parsed)


def _prune_recent_buckets(
    buckets: dict[str, int],
    *,
    at: datetime,
) -> None:
    cutoff = _aware_utc(at) - timedelta(seconds=RECENT_RETENTION_SECONDS)
    for raw_key in list(buckets):
        bucket = _parse_bucket_key(raw_key)
        if bucket is None or bucket + timedelta(seconds=RECENT_BUCKET_SECONDS) <= cutoff:
            buckets.pop(raw_key, None)


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
            "recent_voice_buckets": {},
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
        if version not in {1, REFUGE_ACTIVITY_SCHEMA_VERSION}:
            raise RefugeActivitySchemaError(
                "unsupported refuge activity schema "
                f"{version}; expected 1 or {REFUGE_ACTIVITY_SCHEMA_VERSION}"
            )
        migrated = version == 1

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

        recent_voice_buckets: dict[str, int] = {}
        raw_buckets = raw.get("recent_voice_buckets", {})
        if isinstance(raw_buckets, Mapping):
            for raw_key, raw_seconds in raw_buckets.items():
                bucket = _parse_bucket_key(raw_key)
                if bucket is None:
                    continue
                try:
                    seconds = max(0, int(raw_seconds))
                except (TypeError, ValueError):
                    continue
                if seconds <= 0:
                    continue
                key = bucket.isoformat()
                recent_voice_buckets[key] = (
                    recent_voice_buckets.get(key, 0) + seconds
                )

        tracking_started_at = raw.get("tracking_started_at")
        self._data = {
            "schema_version": REFUGE_ACTIVITY_SCHEMA_VERSION,
            "tracking_started_at": (
                str(tracking_started_at) if tracking_started_at else None
            ),
            "community_voice_seconds": total_seconds,
            "seasons": seasons,
            "recent_voice_buckets": recent_voice_buckets,
        }
        self._loaded = True
        if migrated:
            await atomic_write_json_async(self.path, self._data)

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

            recent = self._data.setdefault("recent_voice_buckets", {})
            for bucket_key, seconds in _split_recent_buckets(
                started_at,
                recorded_seconds,
            ):
                recent[bucket_key] = int(recent.get(bucket_key, 0)) + seconds
            _prune_recent_buckets(
                recent,
                at=_aware_utc(started_at) + timedelta(seconds=recorded_seconds),
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

    async def get_recent_seconds(
        self,
        *,
        window_seconds: int = 24 * 60 * 60,
        at: datetime | None = None,
    ) -> int:
        window = int(window_seconds)
        if window <= 0:
            raise ValueError("window_seconds must be > 0")

        now = _aware_utc(at)
        cutoff = now - timedelta(seconds=window)
        snapshot = await self.get_snapshot()
        raw_buckets = snapshot.get("recent_voice_buckets", {})
        if not isinstance(raw_buckets, Mapping):
            return 0

        total = 0
        for raw_key, raw_seconds in raw_buckets.items():
            bucket = _parse_bucket_key(raw_key)
            if bucket is None:
                continue
            bucket_end = bucket + timedelta(seconds=RECENT_BUCKET_SECONDS)
            if bucket_end <= cutoff or bucket > now:
                continue
            try:
                total += max(0, int(raw_seconds))
            except (TypeError, ValueError):
                continue
        return total

    async def flush(self) -> None:
        async with self._get_lock():
            await self._load_locked()
            if not self._dirty:
                return
            await atomic_write_json_async(self.path, self._data)
            self._dirty = False


refuge_activity_store = RefugeActivityStore()


__all__ = [
    "RECENT_BUCKET_SECONDS",
    "RECENT_RETENTION_SECONDS",
    "REFUGE_ACTIVITY_FILE",
    "REFUGE_ACTIVITY_SCHEMA_VERSION",
    "RefugeActivitySchemaError",
    "RefugeActivityStore",
    "refuge_activity_store",
]
