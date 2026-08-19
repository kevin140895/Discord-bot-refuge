from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.casino_legends import (
    CASINO_LEGEND_RULES_PATCH,
    CASINO_LEGEND_V21_EVENT_ID,
    CasinoLegendService,
    casino_legend_state_from_status,
)
from services.refuge_casino import RefugeCasinoService
from services.refuge_world import RefugeWorldService
from storage.refuge_casino_activity_store import RefugeCasinoActivityStore
from storage.refuge_world_store import RefugeWorldStore
from storage.roulette_history_store import RouletteHistoryStore
from storage.roulette_legend_store import RouletteLegendStore


NOW = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)


def _services(tmp_path, db_path):
    state_file = tmp_path / "pari_xp_state.json"
    state_file.write_text("{}", encoding="utf-8")
    activity = RefugeCasinoActivityStore(tmp_path / "casino_activity.json")
    world_store = RefugeWorldStore(tmp_path / "refuge_world.json")
    world_service = RefugeWorldService(world_store)
    casino_service = RefugeCasinoService(
        activity_store=activity,
        world_service=world_service,
        state_file=state_file,
    )
    legend_store = RouletteLegendStore(db_path)
    legend_service = CasinoLegendService(
        store=legend_store,
        casino_service=casino_service,
    )
    return casino_service, legend_store, legend_service


def _casino_building(status):
    return next(
        building
        for building in status.state.buildings
        if building.building_id == "casino"
    )


@pytest.mark.asyncio
async def test_v21_resets_false_v2_markers_and_freezes_existing_event_ids(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    casino_service, legend_store, legends = _services(tmp_path, db_path)

    # Old history deliberately qualifies several V2 rules. It must become
    # permanently ineligible once the V2.1 id boundary is established.
    start = NOW - timedelta(hours=2)
    for index in range(15):
        won = index < 8
        await history.record_event(
            user_id=10,
            bet_type="number",
            wager_xp=500,
            payout_xp=5000 if won else 0,
            won=won,
            zero_hit=False,
            selected_number=7,
            drawn_number=7 if won else 8,
            at=start + timedelta(seconds=index),
        )
    for index in range(30):
        await history.record_event(
            user_id=100 + (index % 3),
            bet_type="red",
            wager_xp=150,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=NOW - timedelta(minutes=45) + timedelta(seconds=index),
        )

    boundary = await legend_store.get_max_event_id()
    assert boundary == 45

    # Reproduce production after the first V2 deployment: version 2 exists,
    # but false public markers have already been written back from old history.
    await casino_service.migrate_legend_rules_v2(at=NOW - timedelta(minutes=5))
    for marker in ("black_night", "break_in", "grand_heist"):
        await casino_service.unlock_event(marker, at=NOW - timedelta(minutes=4))

    before = await casino_service.evaluate(at=NOW - timedelta(minutes=3))
    assert set(casino_legend_state_from_status(before).public_events) == {
        "black_night",
        "break_in",
        "grand_heist",
    }

    first = await legends.sync(status=before, at=NOW)
    second = await legends.sync(status=first, at=NOW + timedelta(seconds=1))

    assert casino_legend_state_from_status(first).public_events == ()
    assert casino_legend_state_from_status(second).public_events == ()

    building = _casino_building(second)
    assert building.state["legend_rules_patch"] == CASINO_LEGEND_RULES_PATCH
    assert building.state["legend_rules_v2_after_event_id"] == boundary

    event_ids = [event.event_id for event in second.state.events]
    assert event_ids.count(CASINO_LEGEND_V21_EVENT_ID) == 1
    for marker in ("black_night", "break_in", "grand_heist"):
        assert f"casino:casino_events:{marker}" not in event_ids


@pytest.mark.asyncio
async def test_v21_boundary_ignores_preexisting_rows_regardless_of_iso_offset(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    store = RouletteLegendStore(db_path)

    for index in range(2):
        await history.record_event(
            user_id=77,
            bet_type="number",
            wager_xp=10,
            payout_xp=100,
            won=True,
            zero_hit=False,
            selected_number=7 + index,
            drawn_number=7 + index,
            at=NOW - timedelta(minutes=10 - index),
        )

    boundary = await store.get_max_event_id()
    assert boundary == 2

    # Simulate historical ISO strings with an offset different from the UTC
    # strings written by the current recorder. The id boundary remains exact.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE roulette_events SET occurred_at = ? WHERE id = 1",
            ("2026-08-20T12:00:00+14:00",),
        )
        connection.execute(
            "UPDATE roulette_events SET occurred_at = ? WHERE id = 2",
            ("2026-08-20T12:01:00+14:00",),
        )
        connection.commit()

    evidence = await store.get_evidence(at=NOW, after_event_id=boundary)
    assert evidence.break_in_qualified is False
    assert evidence.max_payout_xp == 0


@pytest.mark.asyncio
async def test_v21_allows_new_events_after_boundary_to_unlock(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service, _legend_store, legends = _services(tmp_path, db_path)

    activated = await legends.sync(at=NOW)
    building = _casino_building(activated)
    assert building.state["legend_rules_patch"] == CASINO_LEGEND_RULES_PATCH
    assert building.state["legend_rules_v2_after_event_id"] == 0

    for index in range(15):
        await history.record_event(
            user_id=500 + index,
            bet_type="black",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=NOW + timedelta(seconds=index + 1),
        )

    unlocked = await legends.sync(at=NOW + timedelta(minutes=1))
    assert "house_always_wins" in casino_legend_state_from_status(
        unlocked
    ).public_events


@pytest.mark.asyncio
async def test_max_event_id_is_zero_without_roulette_table(tmp_path):
    store = RouletteLegendStore(tmp_path / "missing.db")
    assert await store.get_max_event_id() == 0
