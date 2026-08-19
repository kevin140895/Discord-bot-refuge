from datetime import datetime, timedelta, timezone

import pytest

from storage.roulette_history_store import RouletteHistoryStore


@pytest.mark.asyncio
async def test_roulette_history_persists_recent_events_and_reloads(tmp_path):
    path = tmp_path / "refuge.db"
    store = RouletteHistoryStore(path)
    now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)

    first_id = await store.record_event(
        user_id=10,
        bet_type="red",
        wager_xp=20,
        payout_xp=40,
        won=True,
        zero_hit=False,
        at=now,
    )
    second_id = await store.record_event(
        user_id=20,
        bet_type="number",
        wager_xp=10,
        payout_xp=0,
        won=False,
        zero_hit=False,
        selected_number=17,
        drawn_number=23,
        at=now + timedelta(minutes=1),
    )

    snapshot = await store.get_living_snapshot(at=now + timedelta(minutes=2))
    assert [item["id"] for item in snapshot["recent"]] == [second_id, first_id]
    assert snapshot["recent"][0]["selected_number"] == 17
    assert snapshot["recent"][0]["drawn_number"] == 23

    reopened = RouletteHistoryStore(path)
    reloaded = await reopened.get_living_snapshot(at=now + timedelta(minutes=2))
    assert [item["id"] for item in reloaded["recent"]] == [second_id, first_id]


@pytest.mark.asyncio
async def test_roulette_history_builds_spotlight_biggest_win_and_streak(tmp_path):
    store = RouletteHistoryStore(tmp_path / "refuge.db")
    now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)

    await store.record_event(
        user_id=10,
        bet_type="red",
        wager_xp=100,
        payout_xp=200,
        won=True,
        zero_hit=False,
        at=now,
    )
    await store.record_event(
        user_id=10,
        bet_type="black",
        wager_xp=50,
        payout_xp=100,
        won=True,
        zero_hit=False,
        at=now + timedelta(minutes=1),
    )
    await store.record_event(
        user_id=20,
        bet_type="number",
        wager_xp=50,
        payout_xp=500,
        won=True,
        zero_hit=False,
        selected_number=8,
        drawn_number=8,
        at=now + timedelta(minutes=2),
    )
    await store.record_event(
        user_id=20,
        bet_type="even",
        wager_xp=500,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=now + timedelta(minutes=3),
    )

    snapshot = await store.get_living_snapshot(at=now + timedelta(minutes=4))

    assert snapshot["spotlight"] == {
        "user_id": 10,
        "bets": 2,
        "wins": 2,
        "wagered_xp": 150,
        "payout_xp": 300,
        "net_xp": 150,
    }
    assert snapshot["biggest_win"]["user_id"] == 20
    assert snapshot["biggest_win"]["payout_xp"] == 500
    assert snapshot["streak"] is None

    await store.record_event(
        user_id=30,
        bet_type="odd",
        wager_xp=10,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=now + timedelta(minutes=5),
    )
    await store.record_event(
        user_id=31,
        bet_type="red",
        wager_xp=10,
        payout_xp=0,
        won=False,
        zero_hit=False,
        at=now + timedelta(minutes=6),
    )

    snapshot = await store.get_living_snapshot(at=now + timedelta(minutes=7))
    assert snapshot["streak"] == {"side": "house", "count": 3}


@pytest.mark.asyncio
async def test_roulette_history_ignores_non_positive_spotlight_and_bounds_input(tmp_path):
    store = RouletteHistoryStore(tmp_path / "refuge.db")
    now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)

    await store.record_event(
        user_id=10,
        bet_type="red",
        wager_xp=50,
        payout_xp=0,
        won=False,
        zero_hit=True,
        drawn_number=0,
        at=now,
    )

    snapshot = await store.get_living_snapshot(at=now + timedelta(minutes=1))
    assert snapshot["spotlight"] is None
    assert snapshot["recent"][0]["zero_hit"] is True

    with pytest.raises(ValueError):
        await store.record_event(
            user_id=10,
            bet_type="unknown",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=False,
            at=now,
        )

    with pytest.raises(ValueError):
        await store.record_event(
            user_id=10,
            bet_type="number",
            wager_xp=10,
            payout_xp=0,
            won=False,
            zero_hit=False,
            selected_number=None,
            at=now,
        )
