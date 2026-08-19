from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from rendering.casino_legends import apply_casino_legend_overlay
from services.casino_legends import (
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


NOW = datetime(2026, 8, 20, 2, 30, tzinfo=timezone.utc)


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


@pytest.mark.asyncio
async def test_house_streak_unlocks_house_legend_once(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    casino_service, legends = _casino_service(tmp_path, db_path)

    for index in range(10):
        await history.record_event(
            user_id=10 + index,
            bet_type="red",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=NOW - timedelta(minutes=10 - index),
        )

    first = await legends.sync(at=NOW)
    second = await legends.sync(status=first, at=NOW)
    first_state = casino_legend_state_from_status(first)
    second_state = casino_legend_state_from_status(second)

    assert "house_always_wins" in first_state.public_events
    assert first_state == second_state
    event_ids = [event.event_id for event in second.state.events]
    assert event_ids.count("casino:casino_events:house_always_wins") == 1


@pytest.mark.asyncio
async def test_number_wins_unlock_heist_break_in_and_diamond(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)

    for index in range(3):
        await history.record_event(
            user_id=42,
            bet_type="number",
            wager_xp=500,
            payout_xp=5000,
            won=True,
            zero_hit=False,
            selected_number=17,
            drawn_number=17,
            at=NOW - timedelta(minutes=3 - index),
        )

    status = await legends.sync(at=NOW)
    state = casino_legend_state_from_status(status)

    assert "grand_heist" in state.public_events
    assert "break_in" in state.public_events
    assert "diamond" in state.secret_events


@pytest.mark.asyncio
async def test_three_zeroes_unlock_black_cat(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)

    for index in range(3):
        await history.record_event(
            user_id=100 + index,
            bet_type="number",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=True,
            selected_number=12,
            drawn_number=0,
            at=NOW - timedelta(minutes=3 - index),
        )

    status = await legends.sync(at=NOW)
    state = casino_legend_state_from_status(status)
    assert "black_cat" in state.secret_events


@pytest.mark.asyncio
async def test_lone_late_night_number_win_unlocks_ghost_player(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)

    # 01:15 UTC = 03:15 Europe/Paris in August.
    event_at = datetime(2026, 8, 20, 1, 15, tzinfo=timezone.utc)
    await history.record_event(
        user_id=777,
        bet_type="number",
        wager_xp=10,
        payout_xp=100,
        won=True,
        zero_hit=False,
        selected_number=7,
        drawn_number=7,
        at=event_at,
    )

    status = await legends.sync(at=event_at + timedelta(minutes=1))
    state = casino_legend_state_from_status(status)
    assert "ghost_player" in state.secret_events


@pytest.mark.asyncio
async def test_black_night_requires_house_dominance_and_multiple_players(tmp_path):
    db_path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(db_path)
    _casino_service_unused, legends = _casino_service(tmp_path, db_path)
    start = datetime(2026, 8, 19, 21, 0, tzinfo=timezone.utc)  # 23:00 Paris

    for index in range(20):
        await history.record_event(
            user_id=1 + (index % 2),
            bet_type="black",
            wager_xp=100,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=start + timedelta(minutes=index),
        )

    status = await legends.sync(at=start + timedelta(minutes=21))
    state = casino_legend_state_from_status(status)
    assert "black_night" in state.public_events


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
