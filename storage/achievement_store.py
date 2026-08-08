from __future__ import annotations

import asyncio
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe


ACHIEVEMENTS_FILE = Path(DATA_DIR) / "achievements.json"


class AchievementStore:
    """Persist unlocked achievements without rewriting on read-only checks."""

    def __init__(self, path: str | Path = ACHIEVEMENTS_FILE) -> None:
        self.path = Path(path)
        self._loaded = False
        self._data: dict[str, Any] = {"schema_version": 1, "users": {}}
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

    async def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        raw = await asyncio.to_thread(read_json_safe, self.path, {})
        users: dict[str, Any] = {}
        if isinstance(raw, dict):
            raw_users = raw.get("users", {})
            if isinstance(raw_users, dict):
                users = {
                    str(user_id): dict(payload)
                    for user_id, payload in raw_users.items()
                    if isinstance(payload, dict)
                }
        self._data = {"schema_version": 1, "users": users}
        self._loaded = True

    async def get_user_achievements(self, user_id: int) -> dict[str, str]:
        """Return a copy of one user's ``achievement_id -> unlocked_at`` map."""

        async with self._get_lock():
            await self._ensure_loaded_locked()
            users = self._data["users"]
            payload = users.get(str(user_id), {})
            return {
                str(achievement_id): str(unlocked_at)
                for achievement_id, unlocked_at in payload.items()
            }

    async def unlock_many(
        self,
        user_id: int,
        achievement_ids: Iterable[str],
        *,
        unlocked_at: datetime | None = None,
    ) -> list[str]:
        """Persist newly unlocked IDs and return only the IDs added this call."""

        unique_ids = list(dict.fromkeys(str(item) for item in achievement_ids))
        if not unique_ids:
            return []

        timestamp = (unlocked_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat()

        async with self._get_lock():
            await self._ensure_loaded_locked()
            users = self._data["users"]
            user_payload = users.setdefault(str(user_id), {})
            newly_unlocked: list[str] = []
            for achievement_id in unique_ids:
                if achievement_id in user_payload:
                    continue
                user_payload[achievement_id] = timestamp
                newly_unlocked.append(achievement_id)

            if newly_unlocked:
                await atomic_write_json_async(self.path, self._data)
            return newly_unlocked


achievement_store = AchievementStore()
