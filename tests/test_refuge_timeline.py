from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from models.refuge_world import (
    RefugeBuildingState,
    RefugeHistoricalEvent,
)
from services.refuge_timeline import (
    TIMELINE_STATE_KEY,
    RefugeTimelineService,
)
from storage.refuge_world_store import RefugeWorldStore


class _Renderer:
    def __init__(self) -> None:
        self.calls = []

    async def render_png_async(self, state, *, context=None):
        self.calls.append((state, context))
        return b"HISTORY"


@pytest.mark.asyncio
async def test_first_sync_activates_current_season_without_backfill(tmp_path):
    store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeTimelineService(world_store=store, renderer=_Renderer())
    at = datetime(2026, 8, 9, 16, 0, tzinfo=timezone.utc)

    result = await service.sync(at=at)

    assert result.changed is True
    assert result.archived_season_id is None
    assert result.state.snapshots == ()
    meta = result.state.state[TIMELINE_STATE_KEY]
    assert meta["active_season_id"] == "2026-08"
    assert meta["last_archived_season_id"] is None


@pytest.mark.asyncio
async def test_paris_month_rollover_freezes_previous_world_state(tmp_path):
    store = RefugeWorldStore(tmp_path / "world.json")
    renderer = _Renderer()
    service = RefugeTimelineService(world_store=store, renderer=renderer)

    await service.sync(at=datetime(2026, 8, 9, 16, 0, tzinfo=timezone.utc))
    current = await store.get_state()
    current = replace(
        current,
        buildings=(
            RefugeBuildingState("fire", level=3, unlocked_at="2026-08-15T12:00:00+00:00"),
            RefugeBuildingState("hall", level=2, unlocked_at="2026-08-20T12:00:00+00:00"),
            RefugeBuildingState("casino", level=1, unlocked_at="2026-08-21T12:00:00+00:00"),
        ),
        events=(
            RefugeHistoricalEvent(
                event_id="aug-event",
                event_type="hall_gallery_marker",
                occurred_at="2026-08-31T20:00:00+00:00",
            ),
            RefugeHistoricalEvent(
                event_id="sep-event",
                event_type="construction_started",
                occurred_at="2026-09-01T00:30:00+00:00",
            ),
        ),
        state={**current.state, "permanent_marker": "august"},
    )
    await store.save_state(current)

    # 22:00 UTC on 31 August is 00:00 on 1 September in Europe/Paris.
    result = await service.sync(at=datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc))

    assert result.archived_season_id == "2026-08"
    assert len(result.state.snapshots) == 1
    snapshot = result.state.snapshots[0]
    assert snapshot.season_id == "2026-08"
    levels = {building.building_id: building.level for building in snapshot.buildings}
    assert levels == {"fire": 3, "hall": 2, "casino": 1}
    assert snapshot.event_ids == ("aug-event",)
    assert snapshot.state["permanent_marker"] == "august"
    assert result.state.state[TIMELINE_STATE_KEY]["active_season_id"] == "2026-09"

    timeline = await service.get_timeline(
        at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    )
    chapter = timeline.selected
    assert chapter is not None
    assert chapter.season_id == "2026-08"
    assert chapter.fire_level == 3
    assert chapter.hall_level == 2
    assert chapter.casino_level == 1
    assert chapter.chapter_event_count == 1
    assert chapter.context.season == "summer"
    assert chapter.context.daypart == "night"

    assert await service.render_chapter_png(chapter) == b"HISTORY"
    assert renderer.calls[-1][0] == chapter.state
    assert renderer.calls[-1][1] == chapter.context


@pytest.mark.asyncio
async def test_archived_snapshot_is_never_recalculated_or_overwritten(tmp_path):
    store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeTimelineService(world_store=store, renderer=_Renderer())

    await service.sync(at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc))
    state = await store.get_state()
    await store.save_state(
        replace(state, buildings=(RefugeBuildingState("fire", level=2),))
    )
    await service.sync(at=datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc))

    archived = (await store.get_state()).snapshots[0]
    september = await store.get_state()
    await store.save_state(
        replace(september, buildings=(RefugeBuildingState("fire", level=5),))
    )
    await service.sync(at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))

    after = await store.get_state()
    assert after.snapshots[0] == archived
    timeline = await service.get_timeline(
        selected_season_id="2026-08",
        at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert timeline.selected is not None
    assert timeline.selected.fire_level == 2


@pytest.mark.asyncio
async def test_long_downtime_does_not_fabricate_missing_months(tmp_path):
    store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeTimelineService(world_store=store, renderer=_Renderer())

    await service.sync(at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc))
    result = await service.sync(at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))

    assert result.archived_season_id == "2026-08"
    assert [snapshot.season_id for snapshot in result.state.snapshots] == ["2026-08"]
    assert result.state.state[TIMELINE_STATE_KEY]["active_season_id"] == "2026-10"


@pytest.mark.asyncio
async def test_selected_unknown_season_falls_back_to_latest_archive(tmp_path):
    store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeTimelineService(world_store=store, renderer=_Renderer())

    await service.sync(at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc))
    await service.sync(at=datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc))

    timeline = await service.get_timeline(
        selected_season_id="1999-01",
        at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert timeline.selected_season_id == "2026-08"
