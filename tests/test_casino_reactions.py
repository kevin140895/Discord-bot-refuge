from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from rendering.casino_reactions import apply_casino_reaction_overlay
from services.casino_reactions import (
    CasinoReactionService,
    CasinoReactionState,
    build_casino_reaction_state,
    casino_reaction_override,
)
from storage.roulette_history_store import RouletteHistoryStore
from storage.roulette_reaction_store import RouletteReactionStore


NOW = datetime(2026, 8, 20, 0, 30, tzinfo=timezone.utc)


def _snapshot(**overrides):
    base = {
        "bets_10m": 0,
        "unique_players_10m": 0,
        "latest_event_at": None,
        "latest_zero_at": None,
        "latest_big_win_at": None,
        "latest_big_win_payout_xp": 0,
        "streak_side": None,
        "streak_count": 0,
        "streak_at": None,
    }
    base.update(overrides)
    return base


def test_activity_thresholds_are_discrete_and_cache_friendly():
    assert build_casino_reaction_state(_snapshot(bets_10m=2), at=NOW).activity == "calm"
    assert build_casino_reaction_state(_snapshot(bets_10m=3), at=NOW).activity == "active"
    assert build_casino_reaction_state(_snapshot(bets_10m=8), at=NOW).activity == "busy"
    assert (
        build_casino_reaction_state(
            _snapshot(bets_10m=4, unique_players_10m=4), at=NOW
        ).activity
        == "busy"
    )


def test_exceptional_reaction_uses_most_recent_event():
    state = build_casino_reaction_state(
        _snapshot(
            bets_10m=6,
            latest_zero_at=(NOW - timedelta(minutes=4)).isoformat(),
            latest_big_win_at=(NOW - timedelta(minutes=1)).isoformat(),
        ),
        at=NOW,
    )
    assert state.activity == "active"
    assert state.reaction == "royal_win"


def test_strong_streak_requires_five_and_expires_after_idle_window():
    fresh = build_casino_reaction_state(
        _snapshot(
            streak_side="players",
            streak_count=5,
            streak_at=(NOW - timedelta(minutes=14)).isoformat(),
        ),
        at=NOW,
    )
    stale = build_casino_reaction_state(
        _snapshot(
            streak_side="house",
            streak_count=8,
            streak_at=(NOW - timedelta(minutes=16)).isoformat(),
        ),
        at=NOW,
    )
    short = build_casino_reaction_state(
        _snapshot(
            streak_side="house",
            streak_count=4,
            streak_at=(NOW - timedelta(minutes=1)).isoformat(),
        ),
        at=NOW,
    )
    assert fresh.reaction == "players_streak"
    assert stale.reaction == "none"
    assert short.reaction == "none"


def test_cache_key_ignores_exact_bet_counts_inside_same_visual_band():
    first = CasinoReactionState(activity="active", bets_10m=3, unique_players_10m=1)
    second = CasinoReactionState(activity="active", bets_10m=7, unique_players_10m=2)
    assert first.cache_key == second.cache_key == "active-none"


@pytest.mark.parametrize(
    ("override", "activity", "reaction"),
    [
        ("normal", "calm", "none"),
        ("active", "active", "none"),
        ("busy", "busy", "none"),
        ("green_zero", "active", "green_zero"),
        ("royal_win", "active", "royal_win"),
        ("players_streak", "active", "players_streak"),
        ("house_streak", "active", "house_streak"),
    ],
)
def test_preview_reaction_overrides(override, activity, reaction):
    state = casino_reaction_override(override)
    assert state.activity == activity
    assert state.reaction == reaction


@pytest.mark.asyncio
async def test_reaction_store_reads_existing_lot2_sqlite_history(tmp_path):
    path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(path)
    reaction_store = RouletteReactionStore(path)
    service = CasinoReactionService(reaction_store)

    for index in range(5):
        await history.record_event(
            user_id=100 + index,
            bet_type="red",
            wager_xp=10,
            payout_xp=20,
            won=True,
            zero_hit=False,
            at=NOW - timedelta(minutes=5 - index),
        )

    state = await service.evaluate(at=NOW)
    assert state.activity == "busy"
    assert state.reaction == "players_streak"
    assert state.streak_count == 5


@pytest.mark.asyncio
async def test_recent_zero_and_big_win_are_read_without_new_persistence(tmp_path):
    path = tmp_path / "refuge.db"
    history = RouletteHistoryStore(path)
    reaction_store = RouletteReactionStore(path)
    service = CasinoReactionService(reaction_store)

    await history.record_event(
        user_id=1,
        bet_type="number",
        wager_xp=100,
        payout_xp=0,
        won=False,
        zero_hit=True,
        selected_number=17,
        drawn_number=0,
        at=NOW - timedelta(minutes=3),
    )
    zero_state = await service.evaluate(at=NOW)
    assert zero_state.reaction == "green_zero"

    await history.record_event(
        user_id=2,
        bet_type="number",
        wager_xp=100,
        payout_xp=1000,
        won=True,
        zero_hit=False,
        selected_number=17,
        drawn_number=17,
        at=NOW - timedelta(minutes=1),
    )
    win_state = await service.evaluate(at=NOW)
    assert win_state.reaction == "royal_win"


def test_visual_overlays_are_deterministic_and_distinct():
    base_image = Image.new("RGB", (1280, 720), (20, 20, 24))
    base = io.BytesIO()
    base_image.save(base, format="PNG")
    payload = base.getvalue()

    normal = apply_casino_reaction_overlay(payload, casino_reaction_override("normal"))
    busy_first = apply_casino_reaction_overlay(payload, casino_reaction_override("busy"))
    busy_second = apply_casino_reaction_overlay(payload, casino_reaction_override("busy"))
    zero = apply_casino_reaction_overlay(payload, casino_reaction_override("green_zero"))
    royal = apply_casino_reaction_overlay(payload, casino_reaction_override("royal_win"))

    assert normal == payload
    assert busy_first == busy_second
    assert busy_first != payload
    assert zero != busy_first
    assert royal != zero
    with Image.open(io.BytesIO(royal)) as image:
        assert image.size == (1280, 720)
        assert image.mode == "RGB"
