from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from rendering.casino_legends import apply_casino_legend_overlay
from services.casino_legends import (
    CASINO_LEGEND_RULES_VERSION,
    CasinoLegendService,
    CasinoLegendState,
    casino_legend_state_from_status,
)
from services.refuge_casino import RefugeCasinoService
from services.refuge_world import RefugeWorldService
from storage.refuge_casino_activity_store import RefugeCasinoActivityStore
from storage.refuge_world_store import RefugeWorldStore
from storage.roulette_history_store import RouletteHistoryStore
from storage.roulette_legend_store import RouletteLegendStore


NOW = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)


def _casino_service(tmp_path, db_path):
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
    legend_service = CasinoLegendService(
        store=RouletteLegendStore(db_path),
        casino_service=casino_service,
    )
    return casino_service, legend_service


def _casino_building(status):
    return next(
        building
        for building in status.state.buildings
        if building.building_id == "casino"
    )


async def _activate_v2(legends: CasinoLegendService, *, at: datetime = NOW):
    status = await legends.sync(at=at)
    building = _casino_building(status)
    assert building.state["legend_rules_version"] == CASINO_LEGEND_RULES_VERSION
    assert building.state["legend_rules_v2_started_at"] == at.isoformat()
    return status


@pytest.mark.asyncio
async def test_v2_migration_resets_public_markers_once_and_preserves_secrets(tmp_path):
    db_path = tmp_path / "refuge.db"
    casino_service, legends = _casino_service(tmp_path, db_path)

    for index, marker in enumerate(
        ("grand_heist", "black_night", "break_in", "house_always_wins")
    ):
        await casino_service.unlock_event(marker, at=NOW - timedelta(minutes=10 - index))
    await casino_service.unlock_secret("black_cat", at=NOW - timedelta(minutes=1))

    before = await casino_service.evaluate(at=NOW)
    assert len(casino_legend_state_from_status(before).public_events) == 4

    first = await legends.sync(status=before, at=NOW)
    second = await legends.sync(status=first, at=NOW + timedelta(seconds=1))
    first_state = casino_legend_state_from_status(first)
    second_state = casino_legend_state_from_status(second)

    assert first_state.public_events == ()
    assert first_state.secret_events == ("black_cat",)
    assert first_state == second_state

    building = _casino_building(second)
    assert building.state["legend_rules_version"] == 2
    assert building.state["legend_rules_v2_started_at"] == NOW.isoformat()

    event_ids = [event.event_id for event in second.state.events]
    for marker in ("grand_heist", "black_night", "break_in", "house_always_wins"):
        assert f"casino:casino_events:{marker}" not in event_ids
    assert event_ids.count("casino:legend_rules:v2") == 1
    migration = next(
        event for event in second.state.events if event.event_id == "casino:legend_rules:v2"
    )
    assert migration.data["reset_markers"] == [
        "black_night",
        "break_in",
        "grand_heist",
        "house_always_wins",
    ]


@pytest.mark.asyncio
async def test_pre_v2_history_cannot_immediately_reunlock_legends(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)

    for index in range(20):
        await history.record_event(
            user_id=10,
            bet_type="red",
            wager_xp=500,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=NOW - timedelta(minutes=20 - index),
        )

    status = await legends.sync(at=NOW)
    assert casino_legend_state_from_status(status).public_events == ()


@pytest.mark.asyncio
async def test_house_legend_requires_15_simple_losses_and_ignores_number_losses(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    await _activate_v2(legends)

    for index in range(20):
        await history.record_event(
            user_id=900 + index,
            bet_type="number",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=False,
            selected_number=7,
            drawn_number=11,
            at=NOW + timedelta(seconds=index + 1),
        )
    for index in range(14):
        await history.record_event(
            user_id=10 + index,
            bet_type="red",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=NOW + timedelta(minutes=1, seconds=index),
        )

    before = await legends.sync(at=NOW + timedelta(minutes=2))
    assert "house_always_wins" not in casino_legend_state_from_status(before).public_events

    await history.record_event(
        user_id=999,
        bet_type="black",
        wager_xp=10,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=NOW + timedelta(minutes=2, seconds=1),
    )
    first = await legends.sync(at=NOW + timedelta(minutes=3))
    second = await legends.sync(status=first, at=NOW + timedelta(minutes=4))
    state = casino_legend_state_from_status(second)

    assert "house_always_wins" in state.public_events
    event_ids = [event.event_id for event in second.state.events]
    assert event_ids.count("casino:casino_events:house_always_wins") == 1


@pytest.mark.asyncio
async def test_grand_heist_unlocks_at_8000_net_after_15_bets_and_8_wins(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    await _activate_v2(legends)

    for index in range(15):
        await history.record_event(
            user_id=42,
            bet_type="red",
            wager_xp=500,
            payout_xp=1000,
            won=True,
            zero_hit=False,
            at=NOW + timedelta(minutes=index + 1),
        )

    before = await legends.sync(at=NOW + timedelta(minutes=16))
    assert "grand_heist" not in casino_legend_state_from_status(before).public_events

    await history.record_event(
        user_id=42,
        bet_type="black",
        wager_xp=500,
        payout_xp=1000,
        won=True,
        zero_hit=False,
        at=NOW + timedelta(minutes=17),
    )
    after = await legends.sync(at=NOW + timedelta(minutes=18))
    assert "grand_heist" in casino_legend_state_from_status(after).public_events


@pytest.mark.asyncio
async def test_break_in_requires_two_consecutive_number_wins_by_same_player(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    await _activate_v2(legends)

    await history.record_event(
        user_id=42,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=17,
        drawn_number=17,
        at=NOW + timedelta(minutes=1),
    )
    await history.record_event(
        user_id=99,
        bet_type="red",
        wager_xp=10,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=NOW + timedelta(minutes=2),
    )
    await history.record_event(
        user_id=42,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=8,
        drawn_number=8,
        at=NOW + timedelta(minutes=3),
    )

    before = await legends.sync(at=NOW + timedelta(minutes=4))
    assert "break_in" not in casino_legend_state_from_status(before).public_events

    await history.record_event(
        user_id=42,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=21,
        drawn_number=21,
        at=NOW + timedelta(minutes=5),
    )
    after = await legends.sync(at=NOW + timedelta(minutes=6))
    assert "break_in" in casino_legend_state_from_status(after).public_events


@pytest.mark.asyncio
async def test_black_night_requires_30_bets_4000_house_net_and_three_players(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    start = datetime(2026, 8, 20, 20, 30, tzinfo=timezone.utc)  # 22:30 Paris
    await _activate_v2(legends, at=start - timedelta(minutes=1))

    for index in range(29):
        await history.record_event(
            user_id=1 + (index % 3),
            bet_type="black",
            wager_xp=150,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=start + timedelta(minutes=index),
        )

    before = await legends.sync(at=start + timedelta(minutes=29))
    assert "black_night" not in casino_legend_state_from_status(before).public_events

    await history.record_event(
        user_id=3,
        bet_type="black",
        wager_xp=150,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=start + timedelta(minutes=30),
    )
    after = await legends.sync(at=start + timedelta(minutes=31))
    assert "black_night" in casino_legend_state_from_status(after).public_events


@pytest.mark.asyncio
async def test_black_cat_requires_three_zeroes_inside_twelve_consecutive_spins(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    await _activate_v2(legends)

    for index in range(13):
        zero_hit = index in {0, 6, 12}
        await history.record_event(
            user_id=100 + index,
            bet_type="red",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=zero_hit,
            at=NOW + timedelta(seconds=index + 1),
        )

    before = await legends.sync(at=NOW + timedelta(minutes=1))
    assert "black_cat" not in casino_legend_state_from_status(before).secret_events

    await history.record_event(
        user_id=999,
        bet_type="black",
        wager_xp=10,
        payout_xp=0,
        won=False,
        zero_hit=True,
        at=NOW + timedelta(minutes=1, seconds=1),
    )
    after = await legends.sync(at=NOW + timedelta(minutes=2))
    assert "black_cat" in casino_legend_state_from_status(after).secret_events


@pytest.mark.asyncio
async def test_diamond_requires_maximum_number_win(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    await _activate_v2(legends)

    await history.record_event(
        user_id=55,
        bet_type="number",
        wager_xp=499,
        payout_xp=4990,
        won=True,
        zero_hit=False,
        selected_number=3,
        drawn_number=3,
        at=NOW + timedelta(minutes=1),
    )
    before = await legends.sync(at=NOW + timedelta(minutes=2))
    assert "diamond" not in casino_legend_state_from_status(before).secret_events

    await history.record_event(
        user_id=55,
        bet_type="number",
        wager_xp=500,
        payout_xp=5000,
        won=True,
        zero_hit=False,
        selected_number=22,
        drawn_number=22,
        at=NOW + timedelta(minutes=3),
    )
    after = await legends.sync(at=NOW + timedelta(minutes=4))
    assert "diamond" in casino_legend_state_from_status(after).secret_events


@pytest.mark.asyncio
async def test_ghost_player_requires_two_solitary_late_night_number_wins(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    await _activate_v2(legends)

    # August: UTC+2 in Europe/Paris. These events are between 02:00 and 05:00 local.
    await history.record_event(
        user_id=777,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=7,
        drawn_number=7,
        at=NOW + timedelta(minutes=10),
    )
    one_win = await legends.sync(at=NOW + timedelta(minutes=11))
    assert "ghost_player" not in casino_legend_state_from_status(one_win).secret_events

    await history.record_event(
        user_id=888,
        bet_type="red",
        wager_xp=10,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=NOW + timedelta(minutes=20),
    )
    await history.record_event(
        user_id=777,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=8,
        drawn_number=8,
        at=NOW + timedelta(minutes=30),
    )
    interrupted = await legends.sync(at=NOW + timedelta(minutes=31))
    assert "ghost_player" not in casino_legend_state_from_status(interrupted).secret_events

    await history.record_event(
        user_id=777,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=9,
        drawn_number=9,
        at=NOW + timedelta(minutes=100),
    )
    await history.record_event(
        user_id=777,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=10,
        drawn_number=10,
        at=NOW + timedelta(minutes=120),
    )
    after = await legends.sync(at=NOW + timedelta(minutes=121))
    assert "ghost_player" in casino_legend_state_from_status(after).secret_events


def test_legend_overlay_is_deterministic_and_keeps_casino_dimensions():
    base_image = Image.new("RGB", (1280, 720), (24, 20, 24))
    buffer = io.BytesIO()
    base_image.save(buffer, format="PNG")
    payload = buffer.getvalue()
    legends = CasinoLegendState(
        public_events=("grand_heist", "house_always_wins"),
        secret_events=("black_cat", "diamond", "ghost_player"),
    )

    first = apply_casino_legend_overlay(payload, legends)
    second = apply_casino_legend_overlay(payload, legends)

    assert first == second
    assert first != payload
    with Image.open(io.BytesIO(first)) as image:
        assert image.size == (1280, 720)
        assert image.mode == "RGB"


def test_legend_cache_key_depends_only_on_discovered_markers():
    first = CasinoLegendState(public_events=("grand_heist",))
    second = CasinoLegendState(public_events=("grand_heist",))
    third = CasinoLegendState(public_events=("black_night",))
    assert first.cache_key == second.cache_key
    assert first.cache_key != third.cache_key
