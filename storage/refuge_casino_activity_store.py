from __future__ import annotations

import asyncio
import copy
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe


REFUGE_CASINO_ACTIVITY_SCHEMA_VERSION = 1
REFUGE_CASINO_ACTIVITY_FILE = Path(DATA_DIR) / "refuge_casino_activity.json"
RECENT_RETENTION_SECONDS = 48 * 60 * 60


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _minute_key(at: datetime) -> str:
    return at.replace(second=0, microsecond=0).isoformat()


def _empty_data() -> dict[str, Any]:
    return {
        "schema_version": REFUGE_CASINO_ACTIVITY_SCHEMA_VERSION,
        "tracking_started_at": None,
        "totals": {
            "roulette_wagered_xp": 0,
            "roulette_payout_xp": 0,
            "machine_payout_xp": 0,
            "machine_xp_events": 0,
            "jackpots_500": 0,
            "jackpots_1000": 0,
        },
        "recent_buckets": {},
        "jackpots": [],
    }


class RefugeCasinoActivitySchemaError(RuntimeError):
    pass


class RefugeCasinoActivityStore:
    """Prospective Refuge-only observation of concrete casino XP flows."""

    def __init__(self, path: str | Path = REFUGE_CASINO_ACTIVITY_FILE) -> None:
        self.path = Path(path)
        self._loaded = False
        self._data: dict[str, Any] = _empty_data()
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
        data = _empty_data()
        if isinstance(raw, dict) and raw:
            try:
                schema = int(raw.get("schema_version", 0))
            except (TypeError, ValueError):
                schema = 0
            if schema != REFUGE_CASINO_ACTIVITY_SCHEMA_VERSION:
                raise RefugeCasinoActivitySchemaError(
                    f"unsupported Refuge casino activity schema: {schema}"
                )
            started = raw.get("tracking_started_at")
            if started:
                data["tracking_started_at"] = str(started)
            raw_totals = raw.get("totals", {})
            if isinstance(raw_totals, dict):
                totals = data["totals"]
                for key in totals:
                    try:
                        totals[key] = max(0, int(raw_totals.get(key, 0)))
                    except (TypeError, ValueError):
                        totals[key] = 0
            raw_buckets = raw.get("recent_buckets", {})
            if isinstance(raw_buckets, dict):
                for key, payload in raw_buckets.items():
                    if not isinstance(payload, dict):
                        continue
                    bucket: dict[str, int] = {}
                    for field in (
                        "roulette_wagered_xp",
                        "roulette_payout_xp",
                        "machine_payout_xp",
                        "transactions",
                    ):
                        try:
                            bucket[field] = max(0, int(payload.get(field, 0)))
                        except (TypeError, ValueError):
                            bucket[field] = 0
                    data["recent_buckets"][str(key)] = bucket
            raw_jackpots = raw.get("jackpots", [])
            if isinstance(raw_jackpots, list):
                data["jackpots"] = [
                    dict(item) for item in raw_jackpots if isinstance(item, dict)
                ]
        self._data = data
        self._loaded = True

    def _prune_locked(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=RECENT_RETENTION_SECONDS)
        buckets = self._data["recent_buckets"]
        for key in list(buckets):
            try:
                moment = datetime.fromisoformat(str(key))
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
            except ValueError:
                buckets.pop(key, None)
                continue
            if moment.astimezone(timezone.utc) < cutoff:
                buckets.pop(key, None)

    async def initialize(self, *, at: datetime | None = None) -> dict[str, Any]:
        now = _aware_utc(at)
        async with self._get_lock():
            await self._load_locked()
            changed = False
            if not self._data.get("tracking_started_at"):
                self._data["tracking_started_at"] = now.isoformat()
                changed = True
            self._prune_locked(now)
            if changed:
                await atomic_write_json_async(self.path, self._data)
            return copy.deepcopy(self._data)

    async def record_transaction(
        self,
        *,
        user_id: int,
        source: str,
        requested_amount: int,
        applied_delta: int,
        at: datetime | None = None,
    ) -> None:
        """Record only successful concrete XP movements from existing casino code."""

        normalized_source = str(source).strip()
        if normalized_source not in {"pari_xp", "machine_a_sous"}:
            return
        now = _aware_utc(at)
        async with self._get_lock():
            await self._load_locked()
            if not self._data.get("tracking_started_at"):
                self._data["tracking_started_at"] = now.isoformat()
            self._prune_locked(now)
            totals = self._data["totals"]
            bucket = self._data["recent_buckets"].setdefault(
                _minute_key(now),
                {
                    "roulette_wagered_xp": 0,
                    "roulette_payout_xp": 0,
                    "machine_payout_xp": 0,
                    "transactions": 0,
                },
            )
            bucket["transactions"] = int(bucket.get("transactions", 0)) + 1

            if normalized_source == "pari_xp":
                if applied_delta < 0:
                    value = -int(applied_delta)
                    totals["roulette_wagered_xp"] += value
                    bucket["roulette_wagered_xp"] += value
                elif applied_delta > 0:
                    value = int(applied_delta)
                    totals["roulette_payout_xp"] += value
                    bucket["roulette_payout_xp"] += value
            else:
                totals["machine_xp_events"] += 1
                if applied_delta > 0:
                    value = int(applied_delta)
                    totals["machine_payout_xp"] += value
                    bucket["machine_payout_xp"] += value
                nominal = int(requested_amount)
                if nominal in {500, 1000}:
                    counter = f"jackpots_{nominal}"
                    totals[counter] += 1
                    event_id = (
                        f"machine:{nominal}:{int(user_id)}:"
                        f"{int(now.timestamp() * 1_000_000)}"
                    )
                    self._data["jackpots"].append(
                        {
                            "event_id": event_id,
                            "user_id": int(user_id),
                            "tier": nominal,
                            "nominal_xp": nominal,
                            "applied_xp": max(0, int(applied_delta)),
                            "occurred_at": now.isoformat(),
                        }
                    )

            await atomic_write_json_async(self.path, self._data)

    async def get_snapshot(self, *, at: datetime | None = None) -> dict[str, Any]:
        now = _aware_utc(at)
        async with self._get_lock():
            await self._load_locked()
            self._prune_locked(now)
            return copy.deepcopy(self._data)

    async def get_recent_totals(
        self,
        *,
        window_seconds: int = 24 * 60 * 60,
        at: datetime | None = None,
    ) -> dict[str, int]:
        now = _aware_utc(at)
        cutoff = now - timedelta(seconds=max(1, int(window_seconds)))
        totals = {
            "roulette_wagered_xp": 0,
            "roulette_payout_xp": 0,
            "machine_payout_xp": 0,
            "transactions": 0,
        }
        async with self._get_lock():
            await self._load_locked()
            self._prune_locked(now)
            for key, payload in self._data["recent_buckets"].items():
                try:
                    moment = datetime.fromisoformat(str(key))
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=timezone.utc)
                    moment = moment.astimezone(timezone.utc)
                except ValueError:
                    continue
                if moment < cutoff or moment > now:
                    continue
                for field in totals:
                    totals[field] += max(0, int(payload.get(field, 0)))
        return totals


refuge_casino_activity_store = RefugeCasinoActivityStore()


__all__ = [
    "RECENT_RETENTION_SECONDS",
    "REFUGE_CASINO_ACTIVITY_FILE",
    "REFUGE_CASINO_ACTIVITY_SCHEMA_VERSION",
    "RefugeCasinoActivitySchemaError",
    "RefugeCasinoActivityStore",
    "refuge_casino_activity_store",
]
