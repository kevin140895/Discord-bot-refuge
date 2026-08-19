from __future__ import annotations

import asyncio
import weakref
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from config import DATA_DIR
from models.refuge_world import (
    REFUGE_WORLD_SCHEMA_VERSION,
    RefugeWorldState,
)
from utils.persistence import atomic_write_json_async, read_json_safe


REFUGE_WORLD_FILE = Path(DATA_DIR) / "refuge_world.json"


class RefugeWorldSchemaError(ValueError):
    """Raised when persisted Refuge data is unsupported or unsafe to load."""


def _migrate_payload(raw: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, Mapping):
        raise RefugeWorldSchemaError("refuge world root must be a JSON object")

    payload = {str(key): value for key, value in raw.items()}
    if not payload:
        raise RefugeWorldSchemaError("refuge world persistence is an empty JSON object")
    try:
        version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError):
        version = 0

    if version > REFUGE_WORLD_SCHEMA_VERSION:
        raise RefugeWorldSchemaError(
            "refuge world schema is newer than this bot "
            f"({version} > {REFUGE_WORLD_SCHEMA_VERSION})"
        )

    migrated = False
    while version < REFUGE_WORLD_SCHEMA_VERSION:
        if version == 0:
            payload = {
                "schema_version": 1,
                "created_at": payload.get("created_at"),
                "buildings": payload.get("buildings", {}),
                "events": payload.get(
                    "events",
                    payload.get("historical_events", []),
                ),
                "snapshots": payload.get("snapshots", {}),
                "panel": payload.get("panel", {}),
                "active_construction": payload.get("active_construction"),
                "state": payload.get("state", {}),
            }
            version = 1
            migrated = True
            continue
        raise RefugeWorldSchemaError(
            f"no refuge world migration path from schema {version}"
        )

    return payload, migrated


def _utc_iso(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


class RefugeWorldStore:
    """Persist the Refuge world state without owning progression rules."""

    def __init__(self, path: str | Path = REFUGE_WORLD_FILE) -> None:
        self.path = Path(path)
        self._loaded = False
        self._state = RefugeWorldState()
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

        # A missing primary + missing backup means a genuinely new Refuge.
        # Any existing persistence artifact that cannot be decoded must never
        # be mistaken for an empty world: the Refuge is permanent and a
        # silent reset would be irreversible once a new save rotates backups.
        backup_path = self.path.with_suffix(self.path.suffix + ".bak")
        primary_exists = self.path.exists()
        backup_exists = backup_path.exists()
        raw = await asyncio.to_thread(read_json_safe, self.path, None)
        if raw is None:
            if primary_exists or backup_exists:
                raise RefugeWorldSchemaError(
                    "refuge world persistence is unreadable; refusing to reset "
                    "the permanent world"
                )
            self._state = RefugeWorldState()
            self._loaded = True
            return

        payload, migrated = _migrate_payload(raw)
        try:
            state = RefugeWorldState.from_dict(payload)
        except ValueError as exc:
            raise RefugeWorldSchemaError(str(exc)) from exc

        self._state = state
        self._loaded = True
        if migrated:
            await atomic_write_json_async(self.path, state.to_dict())

    @staticmethod
    def _validate_state(state: RefugeWorldState) -> None:
        if state.schema_version != REFUGE_WORLD_SCHEMA_VERSION:
            raise RefugeWorldSchemaError(
                "cannot persist refuge world schema "
                f"{state.schema_version}; expected {REFUGE_WORLD_SCHEMA_VERSION}"
            )

    async def load(self) -> RefugeWorldState:
        async with self._get_lock():
            await self._load_locked()
            return deepcopy(self._state)

    async def initialize(
        self,
        *,
        created_at: datetime | None = None,
    ) -> RefugeWorldState:
        async with self._get_lock():
            await self._load_locked()
            if self._state.created_at is None:
                self._state = replace(
                    self._state,
                    created_at=_utc_iso(created_at),
                )
                await atomic_write_json_async(self.path, self._state.to_dict())
            return deepcopy(self._state)

    async def get_state(self) -> RefugeWorldState:
        async with self._get_lock():
            await self._load_locked()
            return deepcopy(self._state)

    async def save_state(self, state: RefugeWorldState) -> RefugeWorldState:
        self._validate_state(state)
        async with self._get_lock():
            self._state = deepcopy(state)
            self._loaded = True
            await atomic_write_json_async(self.path, self._state.to_dict())
            return deepcopy(self._state)

    async def update_state(
        self,
        updater: Callable[[RefugeWorldState], RefugeWorldState],
    ) -> RefugeWorldState:
        """Atomically transform and persist the latest Refuge world state."""

        async with self._get_lock():
            await self._load_locked()
            updated = updater(deepcopy(self._state))
            if not isinstance(updated, RefugeWorldState):
                raise TypeError("Refuge world updater must return RefugeWorldState")
            self._validate_state(updated)
            if updated != self._state:
                self._state = deepcopy(updated)
                await atomic_write_json_async(self.path, self._state.to_dict())
            return deepcopy(self._state)


refuge_world_store = RefugeWorldStore()


__all__ = [
    "REFUGE_WORLD_FILE",
    "RefugeWorldSchemaError",
    "RefugeWorldStore",
    "refuge_world_store",
]
