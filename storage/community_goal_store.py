from __future__ import annotations

import asyncio
import uuid
import weakref
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe


COMMUNITY_GOALS_FILE = Path(DATA_DIR) / "community_goals.json"
ACTIVE_STATUS = "active"
TERMINAL_STATUSES = frozenset({"completed", "expired", "cancelled"})


def _automation_copy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): deepcopy(item) for key, item in value.items()}


class CommunityGoalStore:
    """Persist community goals, lifecycle and lightweight automation state."""

    def __init__(self, path: str | Path = COMMUNITY_GOALS_FILE) -> None:
        self.path = Path(path)
        self._loaded = False
        self._data: dict[str, Any] = {
            "schema_version": 1,
            "goals": {},
            "automation": {},
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
        goals: dict[str, Any] = {}
        automation: dict[str, Any] = {}
        if isinstance(raw, dict):
            raw_goals = raw.get("goals", {})
            if isinstance(raw_goals, dict):
                goals = {
                    str(goal_id): dict(payload)
                    for goal_id, payload in raw_goals.items()
                    if isinstance(payload, dict)
                }
            automation = _automation_copy(raw.get("automation"))
        self._data = {
            "schema_version": 1,
            "goals": goals,
            "automation": automation,
        }
        self._loaded = True

    async def create_goal(
        self,
        *,
        metric_key: str,
        target: int,
        baseline_total: int,
        created_by: int,
        ends_at: datetime,
        title: str | None = None,
        reward_text: str | None = None,
        created_at: datetime | None = None,
        source: str = "manual",
        metadata: Mapping[str, Any] | None = None,
        require_no_active: bool = False,
    ) -> dict[str, Any]:
        now = created_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        end = ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end <= now:
            raise ValueError("ends_at must be after created_at")
        if int(target) <= 0:
            raise ValueError("target must be positive")

        normalized_source = str(source).strip().lower() or "manual"
        if normalized_source not in {"manual", "automatic"}:
            raise ValueError("unsupported goal source")

        async with self._get_lock():
            await self._load_locked()
            goals = self._data["goals"]
            active_payloads = [
                payload
                for payload in goals.values()
                if isinstance(payload, dict) and payload.get("status") == ACTIVE_STATUS
            ]
            if require_no_active and active_payloads:
                raise ValueError("an active goal already exists")
            for payload in active_payloads:
                if payload.get("metric_key") == metric_key:
                    raise ValueError("an active goal already exists for this metric")

            goal_id = uuid.uuid4().hex[:10]
            payload = {
                "id": goal_id,
                "metric_key": str(metric_key),
                "target": int(target),
                "baseline_total": int(baseline_total),
                "created_by": int(created_by),
                "created_at": now.astimezone(timezone.utc).isoformat(),
                "ends_at": end.astimezone(timezone.utc).isoformat(),
                "title": str(title).strip() if title else None,
                "reward_text": str(reward_text).strip() if reward_text else None,
                "source": normalized_source,
                "metadata": _automation_copy(metadata),
                "status": ACTIVE_STATUS,
                "completed_at": None,
                "expired_at": None,
                "cancelled_at": None,
                "final_progress": None,
            }
            goals[goal_id] = payload
            await atomic_write_json_async(self.path, self._data)
            return deepcopy(payload)

    async def list_goals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        async with self._get_lock():
            await self._load_locked()
            items = [
                deepcopy(payload)
                for payload in self._data["goals"].values()
                if isinstance(payload, dict)
                and (status is None or payload.get("status") == status)
            ]
        items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return items

    async def finish_goal(
        self,
        goal_id: str,
        *,
        status: str,
        final_progress: int,
        at: datetime | None = None,
    ) -> dict[str, Any] | None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid terminal status")
        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        timestamp = now.astimezone(timezone.utc).isoformat()

        async with self._get_lock():
            await self._load_locked()
            payload = self._data["goals"].get(str(goal_id))
            if not isinstance(payload, dict):
                return None
            if payload.get("status") != ACTIVE_STATUS:
                return deepcopy(payload)
            payload["status"] = status
            payload["final_progress"] = max(0, int(final_progress))
            payload[f"{status}_at"] = timestamp
            await atomic_write_json_async(self.path, self._data)
            return deepcopy(payload)

    async def get_automation_state(self) -> dict[str, Any]:
        async with self._get_lock():
            await self._load_locked()
            return _automation_copy(self._data.get("automation"))

    async def set_automation_state(
        self,
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = _automation_copy(state)
        async with self._get_lock():
            await self._load_locked()
            self._data["automation"] = normalized
            await atomic_write_json_async(self.path, self._data)
            return _automation_copy(normalized)


community_goal_store = CommunityGoalStore()


__all__ = [
    "ACTIVE_STATUS",
    "COMMUNITY_GOALS_FILE",
    "CommunityGoalStore",
    "TERMINAL_STATUSES",
    "community_goal_store",
]
