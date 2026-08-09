from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.refuge_voice_activity import CommunityVoiceTracker, human_member_count
from storage.refuge_activity_store import RefugeActivitySchemaError, RefugeActivityStore


def member(*, bot: bool = False):
    return SimpleNamespace(bot=bot)


def test_human_member_count_excludes_bots():
    assert human_member_count([member(), member(bot=True), member()]) == 2


@pytest.mark.asyncio
async def test_activity_tracking_start_is_stable_after_restart(tmp_path):
    path = tmp_path / "refuge_activity.json"
    first = RefugeActivityStore(path)
    started = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    first_state = await first.initialize(at=started)

    restarted = RefugeActivityStore(path)
    second_state = await restarted.initialize(
        at=started + timedelta(days=1),
    )

    assert first_state["tracking_started_at"] == started.isoformat()
    assert second_state["tracking_started_at"] == started.isoformat()


@pytest.mark.asyncio
async def test_store_splits_community_voice_across_paris_months(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    start = datetime(2026, 8, 31, 21, 59, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=2)

    recorded = await store.record_interval(start, end)

    assert recorded == 120
    assert await store.get_total_seconds() == 120
    assert await store.get_season_seconds("2026-08") == 30
    assert await store.get_season_seconds("2026-09") == 90


@pytest.mark.asyncio
async def test_tracker_starts_only_with_two_humans_and_preserves_session(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    tracker = CommunityVoiceTracker(store)
    start = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)

    await tracker.reconcile_channel(100, [member()], at=start)
    assert tracker.active_channel_ids == frozenset()

    await tracker.reconcile_channel(
        100,
        [member(), member()],
        at=start + timedelta(minutes=1),
    )
    assert tracker.active_channel_ids == frozenset({100})

    # A third human joining does not restart the qualifying interval.
    await tracker.reconcile_channel(
        100,
        [member(), member(), member()],
        at=start + timedelta(minutes=2),
    )
    await tracker.reconcile_channel(
        100,
        [member()],
        at=start + timedelta(minutes=6),
    )

    assert await store.get_total_seconds() == 5 * 60


@pytest.mark.asyncio
async def test_two_qualifying_channels_accumulate_independently(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    tracker = CommunityVoiceTracker(store)
    start = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)

    await tracker.reconcile_snapshot(
        [
            (100, [member(), member()]),
            (200, [member(), member(), member()]),
        ],
        at=start,
    )
    await tracker.stop_all(at=start + timedelta(minutes=10))

    assert await store.get_total_seconds() == 20 * 60


@pytest.mark.asyncio
async def test_snapshot_excludes_configured_channels(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    tracker = CommunityVoiceTracker(store)
    start = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)

    await tracker.reconcile_snapshot(
        [
            (100, [member(), member()]),
            (999, [member(), member()]),
        ],
        excluded_channel_ids={999},
        at=start,
    )

    assert tracker.active_channel_ids == frozenset({100})


@pytest.mark.asyncio
async def test_checkpoint_persists_without_resetting_fractional_time(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    tracker = CommunityVoiceTracker(store)
    start = datetime(2026, 8, 9, 10, 0, 0, 500000, tzinfo=timezone.utc)

    await tracker.reconcile_channel(100, [member(), member()], at=start)
    await tracker.checkpoint(at=start + timedelta(seconds=30.7))
    assert await store.get_total_seconds() == 30

    await tracker.stop_all(at=start + timedelta(seconds=61.2))
    # Fractional remainder from the first checkpoint carries into the second.
    assert await store.get_total_seconds() == 61

    restarted = RefugeActivityStore(tmp_path / "refuge_activity.json")
    assert await restarted.get_total_seconds() == 61


@pytest.mark.asyncio
async def test_reconcile_snapshot_preserves_existing_qualified_session(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    tracker = CommunityVoiceTracker(store)
    start = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)

    await tracker.reconcile_snapshot([(100, [member(), member()])], at=start)
    await tracker.reconcile_snapshot(
        [(100, [member(), member(), member()])],
        at=start + timedelta(minutes=5),
    )
    await tracker.stop_all(at=start + timedelta(minutes=10))

    assert await store.get_total_seconds() == 10 * 60


@pytest.mark.asyncio
async def test_store_rejects_unknown_schema(tmp_path):
    path = tmp_path / "refuge_activity.json"
    path.write_text('{"schema_version": 99}', encoding="utf-8")

    store = RefugeActivityStore(path)
    with pytest.raises(RefugeActivitySchemaError):
        await store.get_snapshot()
