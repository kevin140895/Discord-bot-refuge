from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import cogs.xp as xp
from storage.season_store import SeasonStore
from utils.persistence import read_json_safe
from utils.seasons import (
    rank_rows,
    season_id_for,
    should_count_xp_source,
    split_interval_by_season,
)
from utils.timezones import PARIS_TZ


def test_season_id_uses_paris_calendar_month():
    # 22:30 UTC on July 31 is already August 1 in Paris (UTC+2).
    moment = datetime(2026, 7, 31, 22, 30, tzinfo=timezone.utc)
    assert season_id_for(moment) == "2026-08"


def test_voice_interval_is_split_across_month_boundary():
    start = datetime(2026, 8, 31, 23, 30, tzinfo=PARIS_TZ)
    end = datetime(2026, 9, 1, 0, 30, tzinfo=PARIS_TZ)

    assert split_interval_by_season(start, end) == [
        ("2026-08", 1800),
        ("2026-09", 1800),
    ]


def test_staff_grants_do_not_count_as_competitive_xp():
    assert should_count_xp_source("message", 8) is True
    assert should_count_xp_source("voice_leave", 90) is True
    assert should_count_xp_source("don_xp", 500) is False
    assert should_count_xp_source("message", 0) is False
    assert should_count_xp_source("pari_xp", -100) is False


def test_casino_ranking_keeps_players_with_negative_net_results():
    rows = rank_rows(
        {
            "1": {"casino_bets": 3, "casino_net": -50},
            "2": {"casino_bets": 1, "casino_net": 200},
            "3": {"casino_bets": 0, "casino_net": 999},
        },
        "casino_net",
    )

    assert rows == [("2", 200), ("1", -50)]


@pytest.mark.asyncio
async def test_store_keeps_months_separate_and_persists(tmp_path):
    path = tmp_path / "season_stats.json"
    store = SeasonStore(path)

    await store.record(
        42,
        at=datetime(2026, 8, 31, 20, tzinfo=timezone.utc),
        messages=3,
        xp_earned=24,
    )
    await store.record(
        42,
        at=datetime(2026, 9, 1, 20, tzinfo=timezone.utc),
        messages=2,
        xp_earned=16,
    )
    await store.flush()

    persisted = read_json_safe(path, {})
    assert persisted["seasons"]["2026-08"]["users"]["42"]["messages"] == 3
    assert persisted["seasons"]["2026-09"]["users"]["42"]["messages"] == 2
    assert persisted["seasons"]["2026-08"]["users"]["42"]["xp_earned"] == 24
    assert persisted["seasons"]["2026-09"]["users"]["42"]["xp_earned"] == 16


@pytest.mark.asyncio
async def test_first_casino_snapshot_is_baseline_only_and_survives_restart(tmp_path):
    path = tmp_path / "season_stats.json"
    first = SeasonStore(path)
    moment = datetime(2026, 8, 8, 18, tzinfo=timezone.utc)

    await first.sync_casino_totals(
        {"1": {"bets": 10, "wagered": 1000, "winnings": 1200}},
        at=moment,
    )
    assert await first.get_season("2026-08") is None
    await first.flush()

    restarted = SeasonStore(path)
    await restarted.sync_casino_totals(
        {"1": {"bets": 12, "wagered": 1200, "winnings": 1300}},
        at=moment,
    )

    season = await restarted.get_season("2026-08")
    assert season is not None
    assert season["users"]["1"]["casino_bets"] == 2
    assert season["users"]["1"]["casino_net"] == -100


@pytest.mark.asyncio
async def test_new_casino_player_counts_from_zero_after_global_baseline(tmp_path):
    store = SeasonStore(tmp_path / "season_stats.json")
    moment = datetime(2026, 8, 8, 18, tzinfo=timezone.utc)

    await store.sync_casino_totals(
        {"1": {"bets": 5, "wagered": 500, "winnings": 400}},
        at=moment,
    )
    await store.sync_casino_totals(
        {
            "1": {"bets": 5, "wagered": 500, "winnings": 400},
            "2": {"bets": 1, "wagered": 100, "winnings": 200},
        },
        at=moment,
    )

    season = await store.get_season("2026-08")
    assert season is not None
    assert season["users"]["2"]["casino_bets"] == 1
    assert season["users"]["2"]["casino_net"] == 100


@pytest.mark.asyncio
async def test_award_xp_records_actual_positive_delta(monkeypatch):
    xp.XP_BOOSTS.clear()
    add_xp = AsyncMock(return_value=(2, 2, 200, 208))
    record = AsyncMock()
    monkeypatch.setattr(xp.xp_store, "add_xp", add_xp)
    monkeypatch.setattr(xp.season_store, "record", record)

    result = await xp.award_xp(7, 8, guild_id=123, source="message")

    assert result == (2, 2, 200, 208)
    record.assert_awaited_once()
    kwargs = record.await_args.kwargs
    assert kwargs["xp_earned"] == 8


@pytest.mark.asyncio
async def test_award_xp_excludes_staff_grants(monkeypatch):
    xp.XP_BOOSTS.clear()
    monkeypatch.setattr(
        xp.xp_store,
        "add_xp",
        AsyncMock(return_value=(2, 3, 200, 700)),
    )
    record = AsyncMock()
    monkeypatch.setattr(xp.season_store, "record", record)

    await xp.award_xp(7, 500, guild_id=123, source="don_xp")

    record.assert_not_awaited()
