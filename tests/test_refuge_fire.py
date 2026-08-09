from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.refuge_fire import (
    FIRE_LEVEL_NAMES,
    FIRE_SECRET_EVENTS,
    RefugeFireConfig,
    RefugeFireService,
    fire_intensity_for_recent,
)
from services.refuge_world import RefugeWorldService
from storage.refuge_activity_store import (
    REFUGE_ACTIVITY_SCHEMA_VERSION,
    RefugeActivityStore,
)
from storage.refuge_world_store import RefugeWorldStore


def _fire_building(state):
    return next(
        building
        for building in state.buildings
        if building.building_id == "fire"
    )


def _service(tmp_path):
    activity = RefugeActivityStore(tmp_path / "refuge_activity.json")
    world_store = RefugeWorldStore(tmp_path / "refuge_world.json")
    world_service = RefugeWorldService(world_store)
    fire = RefugeFireService(
        activity_store=activity,
        world_service=world_service,
    )
    return activity, world_store, fire


def test_fire_level_names_are_the_five_validated_names():
    assert FIRE_LEVEL_NAMES == {
        1: "L’Étincelle",
        2: "Le Campement",
        3: "Le Grand Foyer",
        4: "La Place du Refuge",
        5: "Le Cœur du Refuge",
    }


def test_fire_config_accepts_unconfigured_or_four_level_thresholds():
    assert RefugeFireConfig().level_thresholds_seconds == ()
    assert RefugeFireConfig(
        level_thresholds_seconds=(100, 500, 1000, 5000),
        intensity_thresholds_seconds=(120, 900),
    ).level_thresholds_seconds[-1] == 5000

    with pytest.raises(ValueError):
        RefugeFireConfig(level_thresholds_seconds=(100, 500))
    with pytest.raises(ValueError):
        RefugeFireConfig(
            level_thresholds_seconds=(100, 100, 1000, 5000)
        )
    with pytest.raises(ValueError):
        RefugeFireConfig(intensity_thresholds_seconds=(60,))


def test_fire_config_reads_explicit_environment_thresholds(monkeypatch):
    monkeypatch.setenv(
        "REFUGE_FIRE_LEVEL_THRESHOLDS_SECONDS",
        "3600,14400,36000,72000",
    )
    monkeypatch.setenv(
        "REFUGE_FIRE_INTENSITY_THRESHOLDS_SECONDS",
        "1800,10800",
    )

    config = RefugeFireConfig.from_env()

    assert config.level_thresholds_seconds == (3600, 14400, 36000, 72000)
    assert config.intensity_thresholds_seconds == (1800, 10800)


@pytest.mark.parametrize(
    ("recent", "thresholds", "expected"),
    [
        (0, (), "low"),
        (1, (), "normal"),
        (59, (60, 180), "low"),
        (60, (60, 180), "normal"),
        (179, (60, 180), "normal"),
        (180, (60, 180), "high"),
    ],
)
def test_fire_intensity_is_configurable(recent, thresholds, expected):
    assert fire_intensity_for_recent(
        recent,
        thresholds=thresholds,
    ) == expected


@pytest.mark.asyncio
async def test_activity_schema_v1_migrates_without_losing_totals(tmp_path):
    path = tmp_path / "refuge_activity.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tracking_started_at": "2026-08-09T05:00:00+00:00",
                "community_voice_seconds": 4321,
                "seasons": {
                    "2026-08": {"community_voice_seconds": 4321}
                },
            }
        ),
        encoding="utf-8",
    )

    store = RefugeActivityStore(path)
    snapshot = await store.get_snapshot()

    assert snapshot["schema_version"] == REFUGE_ACTIVITY_SCHEMA_VERSION
    assert snapshot["community_voice_seconds"] == 4321
    assert snapshot["seasons"]["2026-08"]["community_voice_seconds"] == 4321
    assert snapshot["recent_voice_buckets"] == {}

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == REFUGE_ACTIVITY_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_recent_voice_window_accumulates_independent_room_time(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    start = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=10)

    await store.record_interval(start, end)
    await store.record_interval(start, end)

    assert await store.get_total_seconds() == 20 * 60
    assert await store.get_recent_seconds(
        at=end,
        window_seconds=24 * 60 * 60,
    ) == 20 * 60


@pytest.mark.asyncio
async def test_recent_voice_window_does_not_backfill_old_activity(tmp_path):
    store = RefugeActivityStore(tmp_path / "refuge_activity.json")
    old = datetime(2026, 8, 7, 4, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)

    await store.record_interval(old, old + timedelta(minutes=20))
    await store.record_interval(now, now + timedelta(minutes=5))

    assert await store.get_total_seconds() == 25 * 60
    assert await store.get_recent_seconds(
        at=now + timedelta(minutes=5),
    ) == 5 * 60


@pytest.mark.asyncio
async def test_fire_starts_at_level_one_without_arbitrary_thresholds(tmp_path):
    activity, _world_store, fire = _service(tmp_path)
    start = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    await activity.record_interval(start, start + timedelta(minutes=2))

    status = await fire.evaluate(
        config=RefugeFireConfig(),
        at=start + timedelta(minutes=2),
    )

    assert status.level == 1
    assert status.level_name == "L’Étincelle"
    assert status.intensity == "normal"
    assert status.lifetime_voice_seconds == 120
    assert _fire_building(status.state).state["intensity"] == "normal"


@pytest.mark.asyncio
async def test_fire_reaches_level_five_and_never_regresses(tmp_path):
    activity, _world_store, fire = _service(tmp_path)
    start = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    config = RefugeFireConfig(
        level_thresholds_seconds=(60, 120, 180, 240),
        intensity_thresholds_seconds=(60, 180),
    )
    await activity.record_interval(start, start + timedelta(seconds=250))

    first = await fire.evaluate(
        config=config,
        at=start + timedelta(seconds=250),
    )
    later = await fire.evaluate(
        config=config,
        at=start + timedelta(days=2),
    )

    assert first.level == 5
    assert first.intensity == "high"
    assert later.level == 5
    assert later.intensity == "low"

    level_events = [
        event
        for event in later.state.events
        if event.event_type == "building_level_reached"
        and event.data.get("building_id") == "fire"
    ]
    assert [event.data["level"] for event in level_events] == [2, 3, 4, 5]


@pytest.mark.asyncio
async def test_fire_secret_unlock_is_persistent_and_idempotent(tmp_path):
    _activity, world_store, fire = _service(tmp_path)
    at = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    config = RefugeFireConfig()

    first = await fire.unlock_secret(
        "first_visitor",
        at=at,
        config=config,
    )
    second = await fire.unlock_secret(
        "first_visitor",
        at=at + timedelta(hours=1),
        config=config,
    )
    restarted = await world_store.get_state()

    assert _fire_building(first).state["secret_events"] == ["first_visitor"]
    assert _fire_building(second).state["secret_events"] == ["first_visitor"]
    assert _fire_building(restarted).state["secret_events"] == ["first_visitor"]

    matching = [
        event
        for event in restarted.events
        if event.event_id == "fire:secret:first_visitor"
    ]
    assert len(matching) == 1
    assert matching[0].data["name"] == FIRE_SECRET_EVENTS["first_visitor"]


@pytest.mark.asyncio
async def test_unknown_fire_secret_is_rejected(tmp_path):
    _activity, _world_store, fire = _service(tmp_path)

    with pytest.raises(ValueError):
        await fire.unlock_secret("not-a-secret")
