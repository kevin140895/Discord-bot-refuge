from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from services.casino_legends import (
    CASINO_LEGEND_RULES_PATCH,
    CASINO_LEGEND_V22_EVENT_ID,
    CasinoLegendService,
    casino_legend_state_from_status,
)
from services.refuge_casino import RefugeCasinoService
from services.refuge_world import RefugeWorldService
from storage.refuge_casino_activity_store import RefugeCasinoActivityStore
from storage.refuge_world_store import RefugeWorldStore
from storage.roulette_history_store import RouletteHistoryStore
from storage.roulette_legend_store import (
    RouletteLegendStore,
    RouletteLegendStoreUnavailable,
)


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
    history_store = RouletteHistoryStore(db_path)
    legend_store = RouletteLegendStore(db_path)
    legend_service = CasinoLegendService(
        store=legend_store,
        casino_service=casino_service,
        history_store=history_store,
    )
    return casino_service, history_store, legend_store, legend_service


def _casino_building(status):
    return next(
        building
        for building in status.state.buildings
        if building.building_id == "casino"
    )


async def _set_v21_metadata(
    casino_service: RefugeCasinoService,
    *,
    boundary: int = 0,
) -> None:
    def updater(state):
        buildings = []
        for building in state.buildings:
            if building.building_id != "casino":
                buildings.append(building)
                continue
            building_state = dict(building.state)
            building_state["legend_rules_version"] = 2
            building_state["legend_rules_patch"] = "2.1"
            building_state["legend_rules_v2_after_event_id"] = boundary
            buildings.append(replace(building, state=building_state))
        return replace(state, buildings=tuple(buildings))

    await casino_service.world_store.update_state(updater)


@pytest.mark.asyncio
async def test_v22_resets_false_v21_markers_and_freezes_existing_event_ids(tmp_path):
    db_path = tmp_path / "refuge.db"
    casino_service, history, legend_store, legends = _services(tmp_path, db_path)

    # Old history deliberately qualifies several rules. V2.2 must capture its
    # maximum id before resetting the false V2.1 markers.
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

    await casino_service.migrate_legend_rules_v2(at=NOW - timedelta(minutes=5))
    await _set_v21_metadata(casino_service, boundary=0)
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
    assert event_ids.count(CASINO_LEGEND_V22_EVENT_ID) == 1
    for marker in ("black_night", "break_in", "grand_heist"):
        assert f"casino:casino_events:{marker}" not in event_ids


@pytest.mark.asyncio
async def test_v22_boundary_ignores_preexisting_rows_regardless_of_iso_offset(tmp_path):
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
async def test_v22_allows_new_events_after_boundary_to_unlock(tmp_path):
    db_path = tmp_path / "refuge.db"
    _casino_service, history, _legend_store, legends = _services(tmp_path, db_path)

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
async def test_max_event_id_requires_verified_roulette_table(tmp_path):
    store = RouletteLegendStore(tmp_path / "missing.db")
    with pytest.raises(RouletteLegendStoreUnavailable):
        await store.get_max_event_id()


@pytest.mark.asyncio
async def test_max_event_id_zero_is_valid_only_after_empty_table_initialization(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    store = RouletteLegendStore(db_path)

    await history.start()
    assert await store.get_max_event_id() == 0


@pytest.mark.asyncio
async def test_v22_operational_error_defers_migration_then_recovers(
    tmp_path,
    monkeypatch,
    caplog,
):
    db_path = tmp_path / "refuge.db"
    casino_service, history, legend_store, legends = _services(tmp_path, db_path)

    await history.record_event(
        user_id=10,
        bet_type="red",
        wager_xp=100,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=NOW - timedelta(minutes=1),
    )
    await casino_service.migrate_legend_rules_v2(at=NOW - timedelta(minutes=5))
    await _set_v21_metadata(casino_service, boundary=0)
    await casino_service.unlock_event("grand_heist", at=NOW - timedelta(minutes=4))
    before = await casino_service.evaluate(at=NOW - timedelta(minutes=3))

    def locked_boundary():
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(legend_store, "_max_event_id_sync", locked_boundary)
        with caplog.at_level(logging.ERROR, logger="services.casino_legends"):
            deferred = await legends.sync(status=before, at=NOW)

    deferred_building = _casino_building(deferred)
    assert deferred_building.state["legend_rules_patch"] == "2.1"
    assert "grand_heist" in casino_legend_state_from_status(deferred).public_events
    assert CASINO_LEGEND_V22_EVENT_ID not in {
        event.event_id for event in deferred.state.events
    }
    assert "V2.2 boundary capture failed; migration deferred" in caplog.text

    recovered = await legends.sync(status=deferred, at=NOW + timedelta(seconds=1))
    recovered_building = _casino_building(recovered)
    assert recovered_building.state["legend_rules_patch"] == CASINO_LEGEND_RULES_PATCH
    assert recovered_building.state["legend_rules_v2_after_event_id"] == 1
    assert casino_legend_state_from_status(recovered).public_events == ()
    assert CASINO_LEGEND_V22_EVENT_ID in {
        event.event_id for event in recovered.state.events
    }
