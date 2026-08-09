from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models.refuge_world import RefugeBuildingState, RefugeWorldState
from services.refuge_secrets import REFUGE_SECRETS_STATE_KEY, RefugeSecretsService
from storage.refuge_world_store import RefugeWorldStore
from utils.achievements import ACHIEVEMENT_BY_ID


class _SnapshotStore:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def get_snapshot(self, **_kwargs):
        return self.snapshot


class _Timeline:
    async def sync_under_world_lock(self, *, at=None):
        return None


async def _service(tmp_path):
    world_store = RefugeWorldStore(tmp_path / "world.json")
    await world_store.save_state(
        RefugeWorldState(
            created_at="2026-08-01T00:00:00+00:00",
            buildings=(
                RefugeBuildingState("casino", level=1),
                RefugeBuildingState("fire", level=1),
                RefugeBuildingState("hall", level=1),
            ),
        )
    )
    voice = _SnapshotStore({"recent_voice_buckets": {}})
    achievements = _SnapshotStore({"users": {}})
    casino = _SnapshotStore({"recent_buckets": {}, "jackpots": []})
    service = RefugeSecretsService(
        world_store=world_store,
        activity_store=voice,
        achievement_store_=achievements,
        casino_activity_store=casino,
        timeline_service=_Timeline(),
    )
    return service, world_store, voice, achievements, casino


def _event_ids(state):
    return {event.event_id for event in state.events}


def _building(state, building_id):
    return next(item for item in state.buildings if item.building_id == building_id)


@pytest.mark.asyncio
async def test_first_sync_is_prospective_and_never_backfills_old_evidence(tmp_path):
    service, world_store, voice, achievements, casino = await _service(tmp_path)
    old = "2026-08-09T10:00:00+00:00"
    voice.snapshot = {"recent_voice_buckets": {old: 60}}
    achievements.snapshot = {"users": {"42": {next(iter(ACHIEVEMENT_BY_ID)): old}}}
    casino.snapshot = {
        "recent_buckets": {
            old: {
                "roulette_wagered_xp": 100,
                "roulette_payout_xp": 100,
                "machine_payout_xp": 0,
                "transactions": 2,
            }
        },
        "jackpots": [
            {
                "event_id": "old",
                "user_id": 42,
                "tier": 1000,
                "occurred_at": old,
            }
        ],
    }

    activated_at = datetime(2026, 8, 9, 16, 50, tzinfo=timezone.utc)
    first = await service.sync(at=activated_at)
    second = await service.sync(
        at=datetime(2026, 8, 9, 16, 51, tzinfo=timezone.utc)
    )

    assert first.discoveries == ()
    assert second.discoveries == ()
    state = await world_store.get_state()
    assert state.events == ()
    assert state.state[REFUGE_SECRETS_STATE_KEY]["enabled_at"] == activated_at.isoformat()


@pytest.mark.asyncio
async def test_fire_discovers_only_from_real_post_activation_dayparts(tmp_path):
    service, world_store, voice, _achievements, _casino = await _service(tmp_path)
    await service.sync(at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc))

    # Europe/Paris in August is UTC+2: 07:00, 12:00, 20:00 and 23:00 local.
    voice.snapshot = {
        "recent_voice_buckets": {
            "2026-08-09T05:00:00+00:00": 60,
            "2026-08-09T10:00:00+00:00": 60,
            "2026-08-09T18:00:00+00:00": 60,
            "2026-08-09T21:00:00+00:00": 60,
        }
    }
    result = await service.sync(
        at=datetime(2026, 8, 9, 21, 1, tzinfo=timezone.utc)
    )

    assert {item.marker_id for item in result.discoveries} >= {
        "first_visitor",
        "night_of_stars",
        "full_circle",
    }
    state = await world_store.get_state()
    assert {
        "fire:secret:first_visitor",
        "fire:secret:night_of_stars",
        "fire:secret:full_circle",
    }.issubset(_event_ids(state))
    assert set(_building(state, "fire").state["secret_events"]) == {
        "first_visitor",
        "night_of_stars",
        "full_circle",
    }

    again = await service.sync(
        at=datetime(2026, 8, 9, 21, 2, tzinfo=timezone.utc)
    )
    assert again.discoveries == ()


@pytest.mark.asyncio
async def test_hall_secrets_use_post_activation_achievement_history(tmp_path):
    service, world_store, _voice, achievements, _casino = await _service(tmp_path)
    await service.sync(at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc))

    by_category = {}
    for achievement_id, definition in ACHIEVEMENT_BY_ID.items():
        by_category.setdefault(definition.category, achievement_id)
    assert by_category

    users = {"42": {}, "99": {}}
    base_time = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    for index, achievement_id in enumerate(by_category.values()):
        user = "42" if index % 2 == 0 else "99"
        users[user][achievement_id] = (base_time + timedelta(minutes=index)).isoformat()
    if not users["99"]:
        used = set(users["42"])
        extra_id = next(
            (item for item in ACHIEVEMENT_BY_ID if item not in used),
            next(iter(ACHIEVEMENT_BY_ID)),
        )
        users["99"][extra_id] = (base_time + timedelta(hours=1)).isoformat()
    achievements.snapshot = {"users": users}

    result = await service.sync(
        at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    )

    assert {item.marker_id for item in result.discoveries} >= {
        "memory_flame",
        "endless_book",
        "forgotten_crown",
    }
    state = await world_store.get_state()
    assert {
        "hall:secret:memory_flame",
        "hall:secret:endless_book",
        "hall:secret:forgotten_crown",
    }.issubset(_event_ids(state))


@pytest.mark.asyncio
async def test_casino_regular_and_secret_markers_are_separate_and_idempotent(tmp_path):
    service, world_store, _voice, _achievements, casino = await _service(tmp_path)
    await service.sync(at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc))

    # 23:00 local provides real night activity. The jackpot timestamps are
    # deliberately inside the configured closed window (07:00 local).
    casino.snapshot = {
        "recent_buckets": {
            "2026-08-09T21:00:00+00:00": {
                "roulette_wagered_xp": 100,
                "roulette_payout_xp": 150,
                "machine_payout_xp": 500,
                "transactions": 4,
            }
        },
        "jackpots": [
            {
                "event_id": "j500",
                "user_id": 42,
                "tier": 500,
                "occurred_at": "2026-08-09T05:00:00+00:00",
            },
            {
                "event_id": "j1000",
                "user_id": 99,
                "tier": 1000,
                "occurred_at": "2026-08-09T05:01:00+00:00",
            },
        ],
    }
    first = await service.sync(
        at=datetime(2026, 8, 9, 21, 5, tzinfo=timezone.utc)
    )

    state = await world_store.get_state()
    casino_building = _building(state, "casino")
    assert {"grand_heist", "black_night", "break_in"}.issubset(
        set(casino_building.state["casino_events"])
    )
    assert {"diamond", "ghost_player"}.issubset(
        set(casino_building.state["secret_events"])
    )
    assert any(item.marker_id == "diamond" for item in first.discoveries)
    for event in state.events:
        if event.event_type.endswith("secret_discovered"):
            assert "condition" not in event.data
            assert "trigger" not in event.data

    again = await service.sync(
        at=datetime(2026, 8, 9, 21, 6, tzinfo=timezone.utc)
    )
    assert again.discoveries == ()


@pytest.mark.asyncio
async def test_casino_break_even_and_house_positive_patterns_unlock_once(tmp_path):
    service, world_store, _voice, _achievements, casino = await _service(tmp_path)
    await service.sync(at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc))

    casino.snapshot = {
        "recent_buckets": {
            "2026-08-09T10:00:00+00:00": {
                "roulette_wagered_xp": 200,
                "roulette_payout_xp": 200,
                "machine_payout_xp": 0,
                "transactions": 4,
            }
        },
        "jackpots": [],
    }
    await service.sync(at=datetime(2026, 8, 9, 10, 5, tzinfo=timezone.utc))
    state = await world_store.get_state()
    assert "black_cat" in _building(state, "casino").state["secret_events"]

    casino.snapshot = {
        "recent_buckets": {
            "2026-08-10T10:00:00+00:00": {
                "roulette_wagered_xp": 300,
                "roulette_payout_xp": 100,
                "machine_payout_xp": 0,
                "transactions": 4,
            }
        },
        "jackpots": [],
    }
    await service.sync(at=datetime(2026, 8, 10, 10, 5, tzinfo=timezone.utc))
    state = await world_store.get_state()
    assert "house_always_wins" in _building(state, "casino").state["casino_events"]
