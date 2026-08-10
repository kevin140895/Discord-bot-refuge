from __future__ import annotations

import asyncio
import weakref
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe


REFUGE_JOURNAL_FILE = Path(DATA_DIR) / "refuge_journal.json"


class RefugeJournalStore:
    """Persist the Journal baseline and publication ledger atomically."""

    def __init__(self, path: str | Path = REFUGE_JOURNAL_FILE) -> None:
        self.path = Path(path)
        self._loaded = False
        self._data: dict[str, Any] = {
            "schema_version": 1,
            "baseline": None,
            "last_issue_number": 0,
            "published": {},
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
        if isinstance(raw, Mapping):
            baseline = raw.get("baseline")
            published = raw.get("published", {})
            try:
                last_issue_number = max(0, int(raw.get("last_issue_number", 0)))
            except (TypeError, ValueError):
                last_issue_number = 0
            self._data = {
                "schema_version": 1,
                "baseline": deepcopy(baseline) if isinstance(baseline, Mapping) else None,
                "last_issue_number": last_issue_number,
                "published": (
                    {str(key): dict(value) for key, value in published.items() if isinstance(value, Mapping)}
                    if isinstance(published, Mapping)
                    else {}
                ),
            }
        self._loaded = True

    async def get_state(self) -> dict[str, Any]:
        async with self._get_lock():
            await self._load_locked()
            return deepcopy(self._data)

    async def ensure_baseline(
        self,
        *,
        captured_at: datetime,
        users: Mapping[str, Mapping[str, int]],
    ) -> bool:
        """Create the first baseline once. Return True only when created."""

        moment = captured_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        async with self._get_lock():
            await self._load_locked()
            if isinstance(self._data.get("baseline"), Mapping):
                return False
            self._data["baseline"] = {
                "captured_at": moment.astimezone(timezone.utc).isoformat(),
                "users": {
                    str(user_id): {str(field): int(value) for field, value in payload.items()}
                    for user_id, payload in users.items()
                    if isinstance(payload, Mapping)
                },
            }
            await atomic_write_json_async(self.path, self._data)
            return True

    async def was_published(self, publication_key: str) -> bool:
        async with self._get_lock():
            await self._load_locked()
            published = self._data.get("published", {})
            return isinstance(published, Mapping) and publication_key in published

    async def commit_publication(
        self,
        *,
        publication_key: str,
        issue_number: int,
        message_id: int,
        published_at: datetime,
        period_start: datetime,
        period_end: datetime,
        users: Mapping[str, Mapping[str, int]],
    ) -> None:
        """Record a successful Discord publication and advance the baseline."""

        published_at_utc = published_at.astimezone(timezone.utc)
        period_start_utc = period_start.astimezone(timezone.utc)
        period_end_utc = period_end.astimezone(timezone.utc)
        async with self._get_lock():
            await self._load_locked()
            published = self._data.setdefault("published", {})
            published[publication_key] = {
                "issue_number": max(1, int(issue_number)),
                "message_id": int(message_id),
                "published_at": published_at_utc.isoformat(),
                "period_start": period_start_utc.isoformat(),
                "period_end": period_end_utc.isoformat(),
            }
            # Keep a bounded ledger while preserving enough history for audits.
            if len(published) > 104:
                ordered = sorted(
                    published.items(),
                    key=lambda item: str(item[1].get("published_at", "")),
                    reverse=True,
                )[:104]
                self._data["published"] = dict(ordered)
            self._data["last_issue_number"] = max(
                int(self._data.get("last_issue_number", 0)),
                int(issue_number),
            )
            self._data["baseline"] = {
                "captured_at": period_end_utc.isoformat(),
                "users": {
                    str(user_id): {str(field): int(value) for field, value in payload.items()}
                    for user_id, payload in users.items()
                    if isinstance(payload, Mapping)
                },
            }
            await atomic_write_json_async(self.path, self._data)


refuge_journal_store = RefugeJournalStore()


__all__ = [
    "REFUGE_JOURNAL_FILE",
    "RefugeJournalStore",
    "refuge_journal_store",
]
