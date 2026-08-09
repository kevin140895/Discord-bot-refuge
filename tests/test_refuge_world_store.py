from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from models.refuge_world import (
    REFUGE_WORLD_SCHEMA_VERSION,
    RefugeBuildingState,
    RefugeConstructionState,
    RefugeHistoricalEvent,
    RefugePanelState,
    RefugeWorldSnapshot,
    RefugeWorldState,
)
from storage.refuge_world_store import RefugeWorldSchemaError, RefugeWorldStore
from utils.persistence import read_json_safe


@pytest.mark.asyncio
async def test_initialize_is_stable_across_restart(tmp_path):
    path = tmp_path / "refuge_world.json"
    created = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)

    first = RefugeWorldStore(path)
    initialized = await first.initialize(created_at=created)

    assert initialized.schema_version == REFUGE_WORLD_SCHEMA_VERSION
    assert initialized.created_at == created.isoformat()

    restarted = RefugeWorldStore(path)
    later = await restarted.initialize(created_at=created + timedelta(days=1))

    assert later == initialized
    assert read_json_safe(path, {})["created_at"] == created.isoformat()


@pytest.mark.asyncio
async def test_round_trip_preserves_world_foundation_state(tmp_path):
    path = tmp_path / "refuge_world.json"
    construction = RefugeConstructionState(
        construction_id="construction-1",
        status="voting",
        project_id="observatory",
        opened_at="2026-08-09T04:00:00+00:00",
        closes_at="2026-08-12T04:00:00+00:00",
        data={"eligible": True},
    )
    buildings = (
        RefugeBuildingState(
            building_id="fire",
            level=2,
            unlocked_at="2026-08-09T04:00:00+00:00",
            state={"intensity": "normal"},
        ),
        RefugeBuildingState(building_id="hall", level=1),
    )
    event = RefugeHistoricalEvent(
        event_id="event-1",
        event_type="world_created",
        occurred_at="2026-08-09T04:00:00+00:00",
        data={"source": "refuge-001"},
    )
    snapshot = RefugeWorldSnapshot(
        season_id="2026-08",
        captured_at="2026-09-01T00:00:00+00:00",
        buildings=buildings,
        event_ids=(event.event_id,),
        active_construction=construction,
        state={"daypart": "night"},
    )
    expected = RefugeWorldState(
        created_at="2026-08-09T04:00:00+00:00",
        buildings=buildings,
        events=(event,),
        snapshots=(snapshot,),
        panel=RefugePanelState(channel_id=123, message_id=456),
        active_construction=construction,
        state={"renderer_revision": 1},
    )

    await RefugeWorldStore(path).save_state(expected)
    restored = await RefugeWorldStore(path).get_state()

    assert restored == expected


@pytest.mark.asyncio
async def test_corrupt_primary_recovers_from_atomic_backup(tmp_path):
    path = tmp_path / "refuge_world.json"
    first_state = RefugeWorldState(created_at="2026-08-09T04:00:00+00:00")
    second_state = RefugeWorldState(
        created_at="2026-08-09T04:00:00+00:00",
        panel=RefugePanelState(channel_id=10, message_id=20),
    )
    store = RefugeWorldStore(path)
    await store.save_state(first_state)
    await store.save_state(second_state)

    path.write_text("{broken", encoding="utf-8")

    recovered = await RefugeWorldStore(path).get_state()

    assert recovered == first_state


@pytest.mark.asyncio
async def test_corrupt_primary_without_backup_is_rejected_without_overwrite(tmp_path):
    path = tmp_path / "refuge_world.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RefugeWorldSchemaError, match="refusing to reset"):
        await RefugeWorldStore(path).get_state()

    assert path.read_text(encoding="utf-8") == "{broken"
    assert not path.with_suffix(".json.bak").exists()


@pytest.mark.asyncio
async def test_non_object_world_payload_is_rejected_without_overwrite(tmp_path):
    path = tmp_path / "refuge_world.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(RefugeWorldSchemaError, match="JSON object"):
        await RefugeWorldStore(path).get_state()

    assert path.read_text(encoding="utf-8") == "[]"


@pytest.mark.asyncio
async def test_unversioned_payload_migrates_to_schema_v1(tmp_path):
    path = tmp_path / "refuge_world.json"
    path.write_text(
        json.dumps(
            {
                "created_at": "2026-08-09T04:00:00+00:00",
                "buildings": {"fire": {"level": 2}},
                "historical_events": [
                    {
                        "event_id": "event-1",
                        "event_type": "world_created",
                        "occurred_at": "2026-08-09T04:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    migrated = await RefugeWorldStore(path).get_state()
    persisted = read_json_safe(path, {})

    assert migrated.schema_version == REFUGE_WORLD_SCHEMA_VERSION
    assert migrated.buildings[0].building_id == "fire"
    assert migrated.buildings[0].level == 2
    assert migrated.events[0].event_id == "event-1"
    assert persisted["schema_version"] == REFUGE_WORLD_SCHEMA_VERSION
    assert "events" in persisted
    assert "historical_events" not in persisted


@pytest.mark.asyncio
async def test_future_schema_is_rejected_without_overwrite(tmp_path):
    path = tmp_path / "refuge_world.json"
    payload = {
        "schema_version": REFUGE_WORLD_SCHEMA_VERSION + 1,
        "created_at": "future",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RefugeWorldSchemaError):
        await RefugeWorldStore(path).get_state()

    assert json.loads(path.read_text(encoding="utf-8")) == payload
