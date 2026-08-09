from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.refuge_construction import (
    CONSTRUCTION_STATUS_BUILDING,
    CONSTRUCTION_STATUS_TIE_BREAK,
    CONSTRUCTION_STATUS_VOTING,
    RefugeConstructionConfig,
    RefugeConstructionService,
)
from storage.community_goal_store import CommunityGoalStore
from storage.refuge_world_store import RefugeWorldStore


UTC = timezone.utc
CONFIG = RefugeConstructionConfig(vote_hours=72, build_hours=168)


async def _complete_goal(
    store: CommunityGoalStore,
    *,
    at: datetime,
    title: str,
    metric_key: str = "messages",
) -> dict:
    created = await store.create_goal(
        metric_key=metric_key,
        target=10,
        baseline_total=0,
        created_by=42,
        created_at=at - timedelta(minutes=1),
        ends_at=at + timedelta(days=2),
        title=title,
    )
    finished = await store.finish_goal(
        str(created["id"]),
        status="completed",
        final_progress=10,
        at=at,
    )
    assert finished is not None
    return finished


@pytest.mark.asyncio
async def test_construction_is_prospective_and_opens_from_new_completed_goal(tmp_path):
    goal_store = CommunityGoalStore(tmp_path / "goals.json")
    world_store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeConstructionService(
        world_store=world_store,
        goal_store=goal_store,
        chooser=lambda choices: choices[0],
    )
    t0 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    await _complete_goal(
        goal_store,
        at=t0 - timedelta(hours=1),
        title="Ancien objectif",
    )
    initial = await service.sync(at=t0, config=CONFIG)
    assert initial.active is False

    goal = await _complete_goal(
        goal_store,
        at=t0 + timedelta(minutes=5),
        title="Nouvel objectif",
    )
    opened = await service.sync(at=t0 + timedelta(minutes=5), config=CONFIG)

    assert opened.active is True
    assert opened.status == CONSTRUCTION_STATUS_VOTING
    assert opened.source_goal_id == goal["id"]
    assert opened.source_goal_title == "Nouvel objectif"
    assert len(opened.options) == 3
    assert opened.allowed_project_ids == (
        "star_observatory",
        "memory_garden",
        "lantern_tower",
    )
    assert opened.final_results == ()

    world = await world_store.get_state()
    assert world.active_construction is not None
    assert any(
        event.event_type == "construction_vote_opened"
        for event in world.events
    )


@pytest.mark.asyncio
async def test_one_member_one_changeable_vote_and_hidden_live_results(tmp_path):
    goal_store = CommunityGoalStore(tmp_path / "goals.json")
    world_store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeConstructionService(
        world_store=world_store,
        goal_store=goal_store,
        chooser=lambda choices: choices[0],
    )
    t0 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    await service.sync(at=t0, config=CONFIG)
    await _complete_goal(goal_store, at=t0 + timedelta(minutes=1), title="Objectif")
    opened_at = t0 + timedelta(minutes=1)
    await service.sync(at=opened_at, config=CONFIG)

    await service.cast_vote(
        100,
        "star_observatory",
        at=opened_at + timedelta(minutes=1),
        config=CONFIG,
    )
    changed = await service.cast_vote(
        100,
        "memory_garden",
        at=opened_at + timedelta(minutes=2),
        config=CONFIG,
    )
    await service.cast_vote(
        200,
        "memory_garden",
        at=opened_at + timedelta(minutes=3),
        config=CONFIG,
    )

    assert changed.user_vote == "memory_garden"
    assert changed.final_results == ()

    state = await world_store.get_state()
    assert state.active_construction is not None
    votes = state.active_construction.data["votes"]
    assert votes == {"100": "memory_garden", "200": "memory_garden"}


@pytest.mark.asyncio
async def test_vote_winner_builds_only_with_elapsed_time_and_becomes_permanent(tmp_path):
    goal_store = CommunityGoalStore(tmp_path / "goals.json")
    world_store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeConstructionService(
        world_store=world_store,
        goal_store=goal_store,
        chooser=lambda choices: choices[0],
    )
    t0 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    await service.sync(at=t0, config=CONFIG)
    goal_at = t0 + timedelta(minutes=1)
    await _complete_goal(goal_store, at=goal_at, title="Objectif")
    await service.sync(at=goal_at, config=CONFIG)
    await service.cast_vote(1, "memory_garden", at=goal_at + timedelta(hours=1), config=CONFIG)
    await service.cast_vote(2, "memory_garden", at=goal_at + timedelta(hours=2), config=CONFIG)
    await service.cast_vote(3, "star_observatory", at=goal_at + timedelta(hours=3), config=CONFIG)

    vote_close = goal_at + timedelta(hours=72)
    building = await service.sync(at=vote_close, config=CONFIG)

    assert building.status == CONSTRUCTION_STATUS_BUILDING
    assert building.project_id == "memory_garden"
    assert building.project_name == "Jardin des Souvenirs"
    assert building.winner_method == "vote"
    assert dict(building.final_results) == {
        "lantern_tower": 0,
        "memory_garden": 2,
        "star_observatory": 1,
    }
    assert building.progress_percent == 0

    halfway = await service.sync(
        at=vote_close + timedelta(hours=84),
        config=CONFIG,
    )
    assert halfway.status == CONSTRUCTION_STATUS_BUILDING
    assert 49 <= halfway.progress_percent <= 50
    state = await world_store.get_state()
    assert state.active_construction is not None
    assert state.active_construction.data["visual_stage"] == 2

    completed = await service.sync(
        at=vote_close + timedelta(hours=168),
        config=CONFIG,
    )
    assert completed.active is False
    assert "Jardin des Souvenirs" in completed.completed_monuments

    state = await world_store.get_state()
    monument = next(
        building
        for building in state.buildings
        if building.building_id == "monument:memory_garden"
    )
    assert monument.level == 1
    assert monument.state["project_name"] == "Jardin des Souvenirs"
    assert any(
        event.event_type == "construction_completed"
        and event.data.get("project_id") == "memory_garden"
        for event in state.events
    )


@pytest.mark.asyncio
async def test_tie_extends_24_hours_then_uses_explicit_random_resolution(tmp_path):
    goal_store = CommunityGoalStore(tmp_path / "goals.json")
    world_store = RefugeWorldStore(tmp_path / "world.json")
    chosen: list[tuple[str, ...]] = []

    def choose(options):
        values = tuple(options)
        chosen.append(values)
        return values[0]

    service = RefugeConstructionService(
        world_store=world_store,
        goal_store=goal_store,
        chooser=choose,
    )
    t0 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    await service.sync(at=t0, config=CONFIG)
    goal_at = t0 + timedelta(minutes=1)
    await _complete_goal(goal_store, at=goal_at, title="Objectif")
    await service.sync(at=goal_at, config=CONFIG)
    await service.cast_vote(1, "star_observatory", at=goal_at + timedelta(hours=1), config=CONFIG)
    await service.cast_vote(2, "memory_garden", at=goal_at + timedelta(hours=2), config=CONFIG)

    tied = await service.sync(
        at=goal_at + timedelta(hours=72),
        config=CONFIG,
    )
    assert tied.status == CONSTRUCTION_STATUS_TIE_BREAK
    assert set(tied.allowed_project_ids) == {"memory_garden", "star_observatory"}
    assert chosen == []

    resolved = await service.sync(
        at=goal_at + timedelta(hours=96),
        config=CONFIG,
    )
    assert resolved.status == CONSTRUCTION_STATUS_BUILDING
    assert resolved.winner_method == "random_tie"
    assert resolved.project_id == "memory_garden"
    assert chosen == [("memory_garden", "star_observatory")]

    state = await world_store.get_state()
    assert any(event.event_type == "construction_vote_tied" for event in state.events)
    started = next(event for event in state.events if event.event_type == "construction_started")
    assert started.data["winner_method"] == "random_tie"


@pytest.mark.asyncio
async def test_completed_goals_queue_without_replacing_active_construction(tmp_path):
    goal_store = CommunityGoalStore(tmp_path / "goals.json")
    world_store = RefugeWorldStore(tmp_path / "world.json")
    service = RefugeConstructionService(
        world_store=world_store,
        goal_store=goal_store,
        chooser=lambda choices: choices[0],
    )
    t0 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    await service.sync(at=t0, config=CONFIG)

    first_at = t0 + timedelta(minutes=1)
    first = await _complete_goal(goal_store, at=first_at, title="Premier")
    opened = await service.sync(at=first_at, config=CONFIG)
    assert opened.source_goal_id == first["id"]

    second_at = t0 + timedelta(minutes=2)
    second = await _complete_goal(goal_store, at=second_at, title="Deuxième")
    still_first = await service.sync(at=second_at, config=CONFIG)
    assert still_first.source_goal_id == first["id"]

    state = await world_store.get_state()
    queued = state.state["construction_pending_goals"]
    assert [item["id"] for item in queued] == [second["id"]]
