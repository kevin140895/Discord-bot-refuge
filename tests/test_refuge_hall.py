from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.refuge_hall import (
    HALL_LEVEL_NAMES,
    HALL_SECRET_EVENTS,
    RefugeHallConfig,
    RefugeHallService,
)
from services.refuge_world import RefugeWorldService
from storage.achievement_store import AchievementStore
from storage.refuge_world_store import RefugeWorldStore


def _hall_building(state):
    return next(
        building
        for building in state.buildings
        if building.building_id == "hall"
    )


def _service(tmp_path):
    achievements = AchievementStore(tmp_path / "achievements.json")
    world_store = RefugeWorldStore(tmp_path / "refuge_world.json")
    world_service = RefugeWorldService(world_store)
    hall = RefugeHallService(
        achievement_store_=achievements,
        world_service=world_service,
    )
    return achievements, world_store, hall


def test_hall_level_names_are_the_five_validated_names():
    assert HALL_LEVEL_NAMES == {
        1: "Cabane des Souvenirs",
        2: "Salle des Trophées",
        3: "Hall des Légendes",
        4: "Panthéon du Refuge",
        5: "Archives Éternelles",
    }


def test_hall_config_keeps_production_progression_unconfigured_by_default():
    config = RefugeHallConfig()
    assert config.level_thresholds_points == ()
    assert config.unlock_weight == 0
    assert config.achiever_weight == 0
    assert config.diversity_weight == 0
    assert config.historical_first_weight == 0
    assert config.rarity_weight == 0

    with pytest.raises(ValueError):
        RefugeHallConfig(level_thresholds_points=(10, 20))
    with pytest.raises(ValueError):
        RefugeHallConfig(level_thresholds_points=(10, 10, 20, 30))
    with pytest.raises(ValueError):
        RefugeHallConfig(unlock_weight=-1)
    with pytest.raises(ValueError):
        RefugeHallConfig(historical_first_achievement_ids=("unknown",))


def test_hall_config_reads_explicit_environment(monkeypatch):
    monkeypatch.setenv("REFUGE_HALL_LEVEL_THRESHOLDS_POINTS", "10,20,40,80")
    monkeypatch.setenv("REFUGE_HALL_UNLOCK_WEIGHT", "2")
    monkeypatch.setenv("REFUGE_HALL_ACHIEVER_WEIGHT", "3")
    monkeypatch.setenv("REFUGE_HALL_DIVERSITY_WEIGHT", "5")
    monkeypatch.setenv("REFUGE_HALL_HISTORICAL_FIRST_WEIGHT", "7")
    monkeypatch.setenv("REFUGE_HALL_RARITY_WEIGHT", "1")
    monkeypatch.setenv("REFUGE_HALL_HISTORICAL_FIRST_IDS", "level_5,casino_1_bet")
    monkeypatch.setenv("REFUGE_HALL_GALLERY_UNLOCK_MILESTONES", "3,10")
    monkeypatch.setenv("REFUGE_HALL_GALLERY_ACHIEVER_MILESTONES", "2,5")

    config = RefugeHallConfig.from_env()

    assert config.level_thresholds_points == (10, 20, 40, 80)
    assert config.unlock_weight == 2
    assert config.historical_first_achievement_ids == ("level_5", "casino_1_bet")
    assert config.gallery_unlock_milestones == (3, 10)
    assert config.gallery_achiever_milestones == (2, 5)


@pytest.mark.asyncio
async def test_achievement_snapshot_is_defensive_and_read_only(tmp_path):
    store = AchievementStore(tmp_path / "achievements.json")
    at = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)
    await store.unlock_many(1, ["level_5"], unlocked_at=at)

    snapshot = await store.get_snapshot()
    snapshot["users"]["1"]["level_5"] = "changed"

    again = await store.get_snapshot()
    assert again["users"]["1"]["level_5"] == at.isoformat()


@pytest.mark.asyncio
async def test_hall_starts_at_level_one_without_calibrated_weights_or_thresholds(tmp_path):
    achievements, _world_store, hall = _service(tmp_path)
    at = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)
    await achievements.unlock_many(1, ["level_5", "casino_1_bet"], unlocked_at=at)

    status = await hall.evaluate(config=RefugeHallConfig(), at=at + timedelta(minutes=1))

    assert status.level == 1
    assert status.level_name == "Cabane des Souvenirs"
    assert status.progression_points == 0
    assert status.signals.total_unlocks == 2
    assert status.signals.unique_achievers == 1


@pytest.mark.asyncio
async def test_hall_uses_real_unlocks_diversity_controlled_firsts_and_relative_rarity(tmp_path):
    achievements, world_store, hall = _service(tmp_path)
    start = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)
    await world_store.initialize(created_at=start)

    await achievements.unlock_many(1, ["level_5"], unlocked_at=start + timedelta(minutes=1))
    await achievements.unlock_many(2, ["level_5"], unlocked_at=start + timedelta(minutes=2))
    await achievements.unlock_many(3, ["level_5"], unlocked_at=start + timedelta(minutes=3))
    await achievements.unlock_many(
        4,
        ["casino_1_bet", "tenure_30_days"],
        unlocked_at=start + timedelta(minutes=4),
    )

    config = RefugeHallConfig(
        historical_first_achievement_ids=("level_5", "casino_1_bet"),
    )
    status = await hall.evaluate(config=config, at=start + timedelta(minutes=5))

    assert status.signals.total_unlocks == 5
    assert status.signals.unique_achievers == 4
    assert status.signals.category_diversity == 3
    assert status.signals.historical_first_count == 2
    assert {
        first.achievement_id: first.user_id
        for first in status.signals.historical_firsts
    } == {"level_5": 1, "casino_1_bet": 4}
    assert status.signals.rare_showcase is not None
    assert status.signals.rare_showcase.achievement_id in {
        "casino_1_bet",
        "tenure_30_days",
    }
    assert status.signals.rare_showcase.unlock_count == 1


@pytest.mark.asyncio
async def test_hall_progression_can_reach_five_and_never_regresses(tmp_path):
    achievements, world_store, hall = _service(tmp_path)
    start = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)
    await world_store.initialize(created_at=start)
    for user_id in range(1, 6):
        await achievements.unlock_many(
            user_id,
            ["level_5"],
            unlocked_at=start + timedelta(minutes=user_id),
        )

    config = RefugeHallConfig(
        level_thresholds_points=(1, 2, 3, 4),
        unlock_weight=1,
    )
    first = await hall.evaluate(config=config, at=start + timedelta(minutes=10))
    assert first.level == 5
    assert first.progression_points == 5

    empty_store = AchievementStore(tmp_path / "empty_achievements.json")
    restarted = RefugeHallService(
        achievement_store_=empty_store,
        world_service=RefugeWorldService(world_store),
    )
    later = await restarted.evaluate(config=config, at=start + timedelta(days=1))
    assert later.level == 5


@pytest.mark.asyncio
async def test_hall_rare_showcase_expires_after_24_hours(tmp_path):
    achievements, world_store, hall = _service(tmp_path)
    start = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)
    await world_store.initialize(created_at=start)
    await achievements.unlock_many(1, ["level_5"], unlocked_at=start)
    await achievements.unlock_many(2, ["casino_1_bet"], unlocked_at=start + timedelta(hours=1))

    recent = await hall.evaluate(
        config=RefugeHallConfig(),
        at=start + timedelta(hours=2),
    )
    expired = await hall.evaluate(
        config=RefugeHallConfig(),
        at=start + timedelta(hours=26),
    )

    assert recent.signals.rare_showcase is not None
    assert _hall_building(recent.state).state.get("rare_showcase") is not None
    assert expired.signals.rare_showcase is None
    assert "rare_showcase" not in _hall_building(expired.state).state


@pytest.mark.asyncio
async def test_hall_builds_season_plaques_only_from_world_history(tmp_path):
    achievements, world_store, hall = _service(tmp_path)
    created = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)
    await world_store.initialize(created_at=created)

    await achievements.unlock_many(
        1,
        ["level_5"],
        unlocked_at=created - timedelta(days=2),
    )
    await achievements.unlock_many(
        2,
        ["casino_1_bet"],
        unlocked_at=created + timedelta(hours=1),
    )
    await achievements.unlock_many(
        3,
        ["tenure_30_days"],
        unlocked_at=datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc),
    )

    status = await hall.evaluate(
        config=RefugeHallConfig(),
        at=datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
    )
    plaques = _hall_building(status.state).state["season_plaques"]

    assert [item["season_id"] for item in plaques] == ["2026-08", "2026-09"]
    assert plaques[0]["unlock_count"] == 1
    assert plaques[1]["unlock_count"] == 1


@pytest.mark.asyncio
async def test_hall_gallery_milestones_are_configurable_and_persistent(tmp_path):
    achievements, world_store, hall = _service(tmp_path)
    start = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)
    await world_store.initialize(created_at=start)
    for user_id, achievement_id in (
        (1, "level_5"),
        (2, "casino_1_bet"),
        (3, "tenure_30_days"),
    ):
        await achievements.unlock_many(
            user_id,
            [achievement_id],
            unlocked_at=start + timedelta(minutes=user_id),
        )

    status = await hall.evaluate(
        config=RefugeHallConfig(
            gallery_unlock_milestones=(3,),
            gallery_achiever_milestones=(2,),
        ),
        at=start + timedelta(minutes=10),
    )
    markers = _hall_building(status.state).state["gallery_markers"]
    ids = {item["marker_id"] for item in markers}

    assert ids == {
        "first_refuge_achievement",
        "achievement_unlock:3",
        "unique_achiever:2",
    }


@pytest.mark.asyncio
async def test_external_gallery_marker_is_idempotent(tmp_path):
    _achievements, _world_store, hall = _service(tmp_path)
    at = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)

    first = await hall.record_gallery_marker(
        "community_goal:first",
        kind="community_goal",
        occurred_at=at,
    )
    second = await hall.record_gallery_marker(
        "community_goal:first",
        kind="community_goal",
        occurred_at=at + timedelta(hours=1),
    )

    assert _hall_building(first).state["gallery_markers"] == _hall_building(second).state["gallery_markers"]
    matching = [
        event
        for event in second.events
        if event.event_id == "hall:gallery:community_goal:first"
    ]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_hall_secret_unlock_is_persistent_and_idempotent(tmp_path):
    _achievements, world_store, hall = _service(tmp_path)
    at = datetime(2026, 8, 9, 5, 0, tzinfo=timezone.utc)

    await hall.unlock_secret("memory_flame", at=at)
    await hall.unlock_secret("memory_flame", at=at + timedelta(hours=1))
    restarted = await world_store.get_state()

    assert _hall_building(restarted).state["secret_events"] == ["memory_flame"]
    matching = [
        event
        for event in restarted.events
        if event.event_id == "hall:secret:memory_flame"
    ]
    assert len(matching) == 1
    assert matching[0].data["name"] == HALL_SECRET_EVENTS["memory_flame"]


@pytest.mark.asyncio
async def test_unknown_hall_secret_is_rejected(tmp_path):
    _achievements, _world_store, hall = _service(tmp_path)

    with pytest.raises(ValueError):
        await hall.unlock_secret("not-a-secret")
