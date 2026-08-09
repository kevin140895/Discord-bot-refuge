from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Iterable

from storage.refuge_activity_store import RefugeActivityStore


MIN_COMMUNITY_HUMANS = 2


def human_member_count(members: Iterable[object]) -> int:
    return sum(1 for member in members if not bool(getattr(member, "bot", False)))


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class CommunityVoiceTracker:
    """Track channel-time where at least two humans share a voice channel."""

    def __init__(self, store: RefugeActivityStore) -> None:
        self.store = store
        self._active_channels: dict[int, datetime] = {}
        self._lock = asyncio.Lock()

    @property
    def active_channel_ids(self) -> frozenset[int]:
        return frozenset(self._active_channels)

    async def reconcile_channel(
        self,
        channel_id: int,
        members: Iterable[object],
        *,
        at: datetime | None = None,
    ) -> None:
        now = _aware_utc(at)
        qualifies = human_member_count(members) >= MIN_COMMUNITY_HUMANS

        async with self._lock:
            started_at = self._active_channels.get(int(channel_id))
            if qualifies:
                if started_at is None:
                    self._active_channels[int(channel_id)] = now
                return

            if started_at is None:
                return
            self._active_channels.pop(int(channel_id), None)
            await self.store.record_interval(started_at, now)

    async def reconcile_snapshot(
        self,
        channels: Iterable[tuple[int, Iterable[object]]],
        *,
        excluded_channel_ids: Iterable[int] = (),
        at: datetime | None = None,
    ) -> None:
        now = _aware_utc(at)
        excluded = {int(channel_id) for channel_id in excluded_channel_ids}
        qualifying = {
            int(channel_id)
            for channel_id, members in channels
            if int(channel_id) not in excluded
            and human_member_count(members) >= MIN_COMMUNITY_HUMANS
        }

        async with self._lock:
            for channel_id in list(self._active_channels):
                if channel_id in qualifying:
                    continue
                started_at = self._active_channels.pop(channel_id)
                await self.store.record_interval(started_at, now)

            for channel_id in qualifying:
                self._active_channels.setdefault(channel_id, now)

    async def checkpoint(
        self,
        *,
        at: datetime | None = None,
    ) -> None:
        now = _aware_utc(at)
        async with self._lock:
            for channel_id, started_at in list(self._active_channels.items()):
                recorded = await self.store.record_interval(started_at, now)
                if recorded > 0:
                    self._active_channels[channel_id] = started_at + timedelta(
                        seconds=recorded
                    )
            await self.store.flush()

    async def stop_all(
        self,
        *,
        at: datetime | None = None,
    ) -> None:
        now = _aware_utc(at)
        async with self._lock:
            for channel_id, started_at in list(self._active_channels.items()):
                await self.store.record_interval(started_at, now)
                self._active_channels.pop(channel_id, None)
            await self.store.flush()


__all__ = [
    "MIN_COMMUNITY_HUMANS",
    "CommunityVoiceTracker",
    "human_member_count",
]
