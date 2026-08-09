from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from models.refuge_world import (
    RefugeBuildingState,
    RefugeHistoricalEvent,
    RefugePanelState,
    RefugeWorldState,
)
from services.refuge_world import (
    BuildingProgressionRule,
    RefugeWorldService,
    level_for_metric,
    world_render_signature,
)
from storage.refuge_world_store import RefugeWorldStore


def test_level_for_metric_uses_injected_thresholds_only():
    thresholds = (100, 500, 1_000, 5_000)

    assert level_for_metric(0, thresholds=thresholds, minimum_level=1) == 1
    assert level_for_metric(99, thresholds=thresholds, minimum_level=1) == 1
    assert level_for_metric(100, thresholds=thresholds, minimum_level=1) == 2
    assert level_for_metric(999, thresholds=thresholds, minimum_level=1) == 3
    assert level_for_metric(5_000, thresholds=thresholds, minimum_level=1) == 5
    assert level_for_metric(999_999, thresholds=thresholds, minimum_level=1) == 5


@pytest.mark.parametrize(
    "thresholds",
    [
        (-1,),
        (100, 100),
        (500, 100),
    ],
)
def test_progression_rule_rejects_invalid_thresholds(thresholds):
    with pytest.raises(ValueError):
        BuildingProgressionRule(
            building_id="fire",
            metric_key="community_voice_seconds",
            thresholds=thresholds,
            minimum_level=1,
        )


@pytest.mark.asyncio
async def test_service_creates_initial_building_without_fake_milestone(tmp_path):
    store = RefugeWorldStore(tmp_path / "refuge_world.json")
    service = RefugeWorldService(store)
    started = datetime(2026, 8, 9, 4, 45, tzinfo=timezone.utc)
    rule = BuildingProgressionRule(
        building_id="fire",
        metric_key="community_voice_seconds",
        thresholds=(100, 500, 1_000, 5_000),
        minimum_level=1,
    )

    result = await service.evaluate(
        metrics={"community_voice_seconds": 0},
        rules=[rule],
        at=started,
    )

    assert result.changed is True
    assert result.changed_buildings == ("fire",)
    assert len(result.state.buildings) == 1
    fire = result.state.buildings[0]
    assert fire.level == 1
    assert fire.unlocked_at == started.isoformat()
    assert result.state.events == ()


@pytest.mark.asyncio
async def test_service_progression_is_monotonic_and_records_crossed_levels_once(tmp_path):
    store = RefugeWorldStore(tmp_path / "refuge_world.json")
    service = RefugeWorldService(store)
    started = datetime(2026, 8, 9, 4, 45, tzinfo=timezone.utc)
    rule = BuildingProgressionRule(
        building_id="fire",
        metric_key="community_voice_seconds",
        thresholds=(100, 500, 1_000, 5_000),
        minimum_level=1,
    )

    await service.evaluate(
        metrics={"community_voice_seconds": 0},
        rules=[rule],
        at=started,
    )
    upgraded = await service.evaluate(
        metrics={"community_voice_seconds": 600},
        rules=[rule],
        at=started + timedelta(days=1),
    )

    assert upgraded.state.buildings[0].level == 3
    assert [event.data["level"] for event in upgraded.state.events] == [2, 3]
    assert all(
        event.event_type == "building_level_reached"
        for event in upgraded.state.events
    )

    lower_metric = await service.evaluate(
        metrics={"community_voice_seconds": 50},
        rules=[rule],
        at=started + timedelta(days=2),
    )

    assert lower_metric.changed is False
    assert lower_metric.state.buildings[0].level == 3
    assert [event.data["level"] for event in lower_metric.state.events] == [2, 3]


@pytest.mark.asyncio
async def test_service_preserves_existing_building_state_when_level_changes(tmp_path):
    store = RefugeWorldStore(tmp_path / "refuge_world.json")
    started = datetime(2026, 8, 9, 4, 45, tzinfo=timezone.utc)
    state = await store.initialize(created_at=started)
    existing = RefugeBuildingState(
        building_id="fire",
        level=2,
        unlocked_at=started.isoformat(),
        state={"decoration": "fox"},
    )
    await store.save_state(replace(state, buildings=(existing,)))

    service = RefugeWorldService(store)
    result = await service.evaluate(
        metrics={"voice": 1_500},
        rules=[
            BuildingProgressionRule(
                building_id="fire",
                metric_key="voice",
                thresholds=(100, 500, 1_000, 5_000),
                minimum_level=1,
            )
        ],
        at=started + timedelta(days=10),
    )

    assert result.state.buildings[0].level == 4
    assert result.state.buildings[0].state == {"decoration": "fox"}


@pytest.mark.asyncio
async def test_service_keeps_unrelated_buildings_and_world_state(tmp_path):
    store = RefugeWorldStore(tmp_path / "refuge_world.json")
    started = datetime(2026, 8, 9, 4, 45, tzinfo=timezone.utc)
    state = await store.initialize(created_at=started)
    hall = RefugeBuildingState(
        building_id="hall",
        level=2,
        unlocked_at=started.isoformat(),
        state={"lights": "gold"},
    )
    seeded = replace(
        state,
        buildings=(hall,),
        state={"season_visual": "summer"},
    )
    await store.save_state(seeded)

    service = RefugeWorldService(store)
    result = await service.evaluate(
        metrics={"voice": 0},
        rules=[
            BuildingProgressionRule(
                building_id="fire",
                metric_key="voice",
                minimum_level=1,
            )
        ],
        at=started,
    )

    buildings = {building.building_id: building for building in result.state.buildings}
    assert buildings["hall"] == hall
    assert buildings["fire"].level == 1
    assert result.state.state == {"season_visual": "summer"}


def test_render_signature_ignores_history_and_panel_but_tracks_visual_state():
    started = "2026-08-09T04:45:00+00:00"
    fire = RefugeBuildingState(
        building_id="fire",
        level=2,
        unlocked_at=started,
        state={"intensity": "normal"},
    )
    state = RefugeWorldState(
        created_at=started,
        buildings=(fire,),
        events=(),
        panel=RefugePanelState(),
        state={"season_visual": "summer"},
    )
    signature = world_render_signature(state)

    metadata_only = replace(
        state,
        panel=RefugePanelState(channel_id=123, message_id=456),
        events=(
            RefugeHistoricalEvent(
                event_id="history",
                event_type="note",
                occurred_at=started,
                data={},
            ),
        ),
    )
    assert world_render_signature(metadata_only) == signature

    visual_change = replace(state, state={"season_visual": "autumn"})
    assert world_render_signature(visual_change) != signature


@pytest.mark.asyncio
async def test_service_rejects_multiple_rules_for_same_building(tmp_path):
    service = RefugeWorldService(
        RefugeWorldStore(tmp_path / "refuge_world.json")
    )

    with pytest.raises(ValueError, match="one progression rule"):
        await service.evaluate(
            metrics={"a": 10, "b": 20},
            rules=[
                BuildingProgressionRule("fire", "a"),
                BuildingProgressionRule("fire", "b"),
            ],
        )
