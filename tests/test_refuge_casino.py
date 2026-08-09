from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from services.refuge_casino import (
    CASINO_EVENTS,
    CASINO_LEVEL_NAMES,
    CASINO_SECRET_EVENTS,
    CasinoSourceSnapshot,
    RefugeCasinoConfig,
    RefugeCasinoService,
    casino_fortune_for_net,
    casino_prestige_points,
    casino_source_snapshot,
)
from services.refuge_world import RefugeWorldService
from storage.refuge_casino_activity_store import RefugeCasinoActivityStore
from storage.refuge_world_store import RefugeWorldStore


def _casino_building(state):
    return next(
        building for building in state.buildings if building.building_id == "casino"
    )


def _service(tmp_path, *, raw_state=None):
    state_file = tmp_path / "pari_xp_state.json"
    state_file.write_text(json.dumps(raw_state or {}), encoding="utf-8")
    activity = RefugeCasinoActivityStore(tmp_path / "refuge_casino_activity.json")
    world_store = RefugeWorldStore(tmp_path / "refuge_world.json")
    world_service = RefugeWorldService(world_store)
    casino = RefugeCasinoService(
        activity_store=activity,
        world_service=world_service,
        state_file=state_file,
    )
    return state_file, activity, world_store, casino


def test_casino_level_names_are_the_five_validated_names():
    assert CASINO_LEVEL_NAMES == {
        1: "Baraque de Jeux",
        2: "Comptoir Chanceux",
        3: "Casino du Refuge",
        4: "Palais du Hasard",
        5: "Maison Éternelle",
    }


def test_casino_config_has_no_production_progression_by_default():
    config = RefugeCasinoConfig()
    assert config.level_thresholds_points == ()
    assert config.roulette_bet_weight == 0
    assert config.roulette_player_weight == 0
    assert config.jackpot_500_weight == 0
    assert config.jackpot_1000_weight == 0
    assert config.fortune_thresholds_xp == ()

    with pytest.raises(ValueError):
        RefugeCasinoConfig(level_thresholds_points=(1, 2))
    with pytest.raises(ValueError):
        RefugeCasinoConfig(level_thresholds_points=(10, 10, 20, 30))
    with pytest.raises(ValueError):
        RefugeCasinoConfig(roulette_bet_weight=-1)
    with pytest.raises(ValueError):
        RefugeCasinoConfig(fortune_thresholds_xp=(-100, 0, 0, 100))


def test_casino_config_reads_only_explicit_environment_values(monkeypatch):
    monkeypatch.setenv("REFUGE_CASINO_LEVEL_THRESHOLDS_POINTS", "10,30,60,100")
    monkeypatch.setenv("REFUGE_CASINO_ROULETTE_BET_WEIGHT", "2")
    monkeypatch.setenv("REFUGE_CASINO_ROULETTE_PLAYER_WEIGHT", "5")
    monkeypatch.setenv("REFUGE_CASINO_JACKPOT_500_WEIGHT", "20")
    monkeypatch.setenv("REFUGE_CASINO_JACKPOT_1000_WEIGHT", "50")
    monkeypatch.setenv("REFUGE_CASINO_FORTUNE_THRESHOLDS_XP", "-500,-50,50,500")

    config = RefugeCasinoConfig.from_env()

    assert config.level_thresholds_points == (10, 30, 60, 100)
    assert config.roulette_bet_weight == 2
    assert config.roulette_player_weight == 5
    assert config.jackpot_500_weight == 20
    assert config.jackpot_1000_weight == 50
    assert config.fortune_thresholds_xp == (-500, -50, 50, 500)


def test_casino_source_snapshot_uses_existing_roulette_counters_only():
    snapshot = casino_source_snapshot(
        {
            "total_bets": 900,
            "total_winnings": 650,
            "players": {
                "1": {"bets": 2, "wagered": 300, "winnings": 200},
                "2": {"bets": 4, "wagered": 600, "winnings": 450},
                "3": {"bets": 0},
            },
        }
    )

    assert snapshot.roulette_bet_count == 6
    assert snapshot.roulette_unique_players == 2
    assert snapshot.roulette_wagered_xp == 900
    assert snapshot.roulette_winnings_xp == 650
    assert snapshot.roulette_house_net_xp == 250


@pytest.mark.parametrize(
    ("net", "transactions", "expected"),
    [
        (-1, 1, "difficulty"),
        (0, 0, "stable"),
        (0, 3, "stable"),
        (1, 1, "prosperous"),
    ],
)
def test_uncalibrated_fortune_uses_only_sign(net, transactions, expected):
    assert casino_fortune_for_net(
        net,
        transactions=transactions,
        thresholds=(),
    ) == expected


@pytest.mark.parametrize(
    ("net", "expected"),
    [
        (-501, "ruined"),
        (-500, "difficulty"),
        (-50, "stable"),
        (50, "prosperous"),
        (500, "insolent"),
    ],
)
def test_calibrated_fortune_supports_all_five_states(net, expected):
    assert casino_fortune_for_net(
        net,
        transactions=1,
        thresholds=(-500, -50, 50, 500),
    ) == expected


@pytest.mark.asyncio
async def test_activity_store_records_concrete_flows_and_machine_jackpot(tmp_path):
    store = RefugeCasinoActivityStore(tmp_path / "casino_activity.json")
    at = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    await store.record_transaction(
        user_id=1,
        source="pari_xp",
        requested_amount=-100,
        applied_delta=-100,
        at=at,
    )
    await store.record_transaction(
        user_id=1,
        source="pari_xp",
        requested_amount=200,
        applied_delta=400,
        at=at + timedelta(seconds=1),
    )
    await store.record_transaction(
        user_id=2,
        source="machine_a_sous",
        requested_amount=500,
        applied_delta=1000,
        at=at + timedelta(seconds=2),
    )

    snapshot = await store.get_snapshot(at=at + timedelta(minutes=1))
    recent = await store.get_recent_totals(at=at + timedelta(minutes=1))

    assert snapshot["totals"]["roulette_wagered_xp"] == 100
    assert snapshot["totals"]["roulette_payout_xp"] == 400
    assert snapshot["totals"]["machine_payout_xp"] == 1000
    assert snapshot["totals"]["jackpots_500"] == 1
    assert snapshot["totals"]["jackpots_1000"] == 0
    assert len(snapshot["jackpots"]) == 1
    assert snapshot["jackpots"][0]["nominal_xp"] == 500
    assert snapshot["jackpots"][0]["applied_xp"] == 1000
    assert recent["roulette_wagered_xp"] == 100
    assert recent["roulette_payout_xp"] == 400
    assert recent["machine_payout_xp"] == 1000


@pytest.mark.asyncio
async def test_recent_activity_expires_without_erasing_lifetime_totals(tmp_path):
    store = RefugeCasinoActivityStore(tmp_path / "casino_activity.json")
    at = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    await store.record_transaction(
        user_id=1,
        source="pari_xp",
        requested_amount=-100,
        applied_delta=-100,
        at=at,
    )

    recent = await store.get_recent_totals(at=at + timedelta(days=3))
    snapshot = await store.get_snapshot(at=at + timedelta(days=3))

    assert recent["transactions"] == 0
    assert snapshot["totals"]["roulette_wagered_xp"] == 100


def test_prestige_points_use_only_explicit_weights():
    source = CasinoSourceSnapshot(
        roulette_bet_count=10,
        roulette_unique_players=3,
        roulette_wagered_xp=1000,
        roulette_winnings_xp=800,
    )
    activity = {
        "totals": {
            "jackpots_500": 2,
            "jackpots_1000": 1,
        }
    }

    assert casino_prestige_points(source, activity, RefugeCasinoConfig()) == 0
    assert casino_prestige_points(
        source,
        activity,
        RefugeCasinoConfig(
            roulette_bet_weight=2,
            roulette_player_weight=5,
            jackpot_500_weight=20,
            jackpot_1000_weight=50,
        ),
    ) == 125


@pytest.mark.asyncio
async def test_casino_starts_level_one_and_fortune_uses_recent_exact_net(tmp_path):
    raw = {
        "total_bets": 1000,
        "total_winnings": 800,
        "players": {"1": {"bets": 10}},
    }
    _state_file, activity, _world_store, casino = _service(tmp_path, raw_state=raw)
    at = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    await activity.record_transaction(
        user_id=1,
        source="pari_xp",
        requested_amount=-100,
        applied_delta=-100,
        at=at,
    )
    await activity.record_transaction(
        user_id=1,
        source="pari_xp",
        requested_amount=200,
        applied_delta=200,
        at=at + timedelta(seconds=1),
    )

    status = await casino.evaluate(config=RefugeCasinoConfig(), at=at + timedelta(minutes=1))

    assert status.level == 1
    assert status.level_name == "Baraque de Jeux"
    assert status.prestige_points == 0
    assert status.roulette_lifetime_house_net_xp == 200
    assert status.recent_house_net_xp == -100
    assert status.fortune == "difficulty"
    assert _casino_building(status.state).state["fortune"] == "difficulty"


@pytest.mark.asyncio
async def test_casino_reaches_level_five_and_never_regresses(tmp_path):
    raw = {
        "total_bets": 1000,
        "total_winnings": 800,
        "players": {"1": {"bets": 10}, "2": {"bets": 5}},
    }
    state_file, _activity, _world_store, casino = _service(tmp_path, raw_state=raw)
    config = RefugeCasinoConfig(
        level_thresholds_points=(5, 10, 20, 30),
        roulette_bet_weight=2,
        roulette_player_weight=5,
    )
    at = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    first = await casino.evaluate(config=config, at=at)
    state_file.write_text("{}", encoding="utf-8")
    later = await casino.evaluate(config=config, at=at + timedelta(days=1))

    assert first.level == 5
    assert later.level == 5
    level_events = [
        event
        for event in later.state.events
        if event.event_type == "building_level_reached"
        and event.data.get("building_id") == "casino"
    ]
    assert [event.data["level"] for event in level_events] == [2, 3, 4, 5]


@pytest.mark.asyncio
async def test_machine_jackpot_is_imported_to_world_history_once(tmp_path):
    _state_file, activity, _world_store, casino = _service(tmp_path)
    at = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    await activity.record_transaction(
        user_id=42,
        source="machine_a_sous",
        requested_amount=1000,
        applied_delta=1000,
        at=at,
    )

    first = await casino.evaluate(config=RefugeCasinoConfig(), at=at + timedelta(minutes=1))
    second = await casino.evaluate(config=RefugeCasinoConfig(), at=at + timedelta(minutes=2))

    matching = [
        event for event in second.state.events
        if event.event_type == "casino_jackpot_observed"
    ]
    assert len(matching) == 1
    assert matching[0].data["tier"] == 1000
    assert matching[0].data["user_id"] == 42
    assert _casino_building(first.state).state["last_jackpot"]["tier"] == 1000


@pytest.mark.asyncio
async def test_casino_event_and_secret_unlock_are_idempotent(tmp_path):
    _state_file, _activity, _world_store, casino = _service(tmp_path)
    at = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    await casino.unlock_event("grand_heist", at=at, config=RefugeCasinoConfig())
    await casino.unlock_event("grand_heist", at=at + timedelta(minutes=1), config=RefugeCasinoConfig())
    state = await casino.unlock_secret("black_cat", at=at, config=RefugeCasinoConfig())
    state = await casino.unlock_secret("black_cat", at=at + timedelta(minutes=1), config=RefugeCasinoConfig())

    building = _casino_building(state)
    assert building.state["casino_events"] == ["grand_heist"]
    assert building.state["secret_events"] == ["black_cat"]
    assert len([event for event in state.events if event.event_id == "casino:casino_events:grand_heist"]) == 1
    assert len([event for event in state.events if event.event_id == "casino:secret_events:black_cat"]) == 1
    assert CASINO_EVENTS["grand_heist"] == "Le Grand Braquage"
    assert CASINO_SECRET_EVENTS["black_cat"] == "Le Chat Noir"


@pytest.mark.asyncio
async def test_unknown_casino_markers_are_rejected(tmp_path):
    _state_file, _activity, _world_store, casino = _service(tmp_path)
    with pytest.raises(ValueError):
        await casino.unlock_event("unknown")
    with pytest.raises(ValueError):
        await casino.unlock_secret("unknown")
