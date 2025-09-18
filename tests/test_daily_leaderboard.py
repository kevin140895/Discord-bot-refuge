import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import cogs.daily_leaderboard as daily_leaderboard
import cogs.daily_ranking as daily_ranking
import cogs.xp as xp
from cogs.daily_leaderboard import DailyLeaderboard
from cogs.daily_ranking import DailyRankingAndRoles


@pytest.mark.asyncio
async def test_calculate_daily_winners_reads_ranking(monkeypatch):
    date = "2024-01-01"
    payload = {
        "top3": {"msg": [{"id": 1, "count": 5}]},
        "winners": {"msg": 1, "vc": None, "mvp": 1},
    }

    wait_mock = AsyncMock(return_value=payload)
    monkeypatch.setattr(daily_ranking, "wait_for_ranking", wait_mock)

    cog = DailyLeaderboard.__new__(DailyLeaderboard)
    cog._known_winners = set()
    result = await DailyLeaderboard._calculate_daily_winners(cog, date)

    assert result == payload
    wait_mock.assert_awaited_once_with(date, timeout=daily_leaderboard.RANKING_WAIT_TIMEOUT)


@pytest.mark.asyncio
async def test_startup_recovery_waits_for_ranking(tmp_path, monkeypatch):
    date = "2024-01-01"
    winners_file = tmp_path / "daily_winners.json"
    winners_file.write_text("{}")

    monkeypatch.setattr(daily_leaderboard, "DAILY_WINNERS_FILE", str(winners_file))

    ranking_data = {
        "top3": {"msg": [{"id": 42, "count": 99}]},
        "winners": {"msg": 42, "vc": None, "mvp": 42},
    }
    monkeypatch.setattr(
        daily_ranking, "list_cached_rankings", AsyncMock(return_value={date})
    )
    monkeypatch.setattr(daily_ranking, "get_cached_ranking", lambda _: ranking_data)

    bot = SimpleNamespace(wait_until_ready=AsyncMock(return_value=None))
    cog = DailyLeaderboard.__new__(DailyLeaderboard)
    cog.bot = bot
    cog._known_winners = set()

    task = asyncio.create_task(DailyLeaderboard._startup_recovery(cog))
    await task

    saved = json.loads(winners_file.read_text())
    assert saved[date]["winners"]["msg"] == 42
    assert saved[date]["top3"]["msg"][0]["id"] == 42
    assert date in getattr(cog, "_known_winners", set())


@pytest.mark.asyncio
async def test_leaderboard_waits_for_ranking_startup_race(monkeypatch):
    date = "2024-01-02"

    # Fresh ranking cache for the test
    cond = asyncio.Condition()
    monkeypatch.setattr(daily_ranking, "_RANKING_CONDITION", cond)
    monkeypatch.setattr(daily_ranking, "LATEST_RANKINGS", {})

    # Populate XP stats as the ranking cog would expect.
    xp.DAILY_STATS.clear()
    xp.DAILY_STATS[date] = {
        "111": {"messages": 5, "voice": 3600},
        "222": {"messages": 3, "voice": 0},
    }
    xp.DAILY_LOCK = asyncio.Lock()

    leaderboard = DailyLeaderboard.__new__(DailyLeaderboard)

    async def produce_ranking() -> None:
        # Simulate the ranking cog finishing a bit later.
        await asyncio.sleep(0.05)
        async with xp.DAILY_LOCK:
            stats = xp.DAILY_STATS.pop(date, {})
        ranking_cog = DailyRankingAndRoles.__new__(DailyRankingAndRoles)
        ranking = DailyRankingAndRoles._compute_ranking(ranking_cog, stats)
        ranking["date"] = date
        await daily_ranking._record_ranking_result(date, ranking)

    winners_task = asyncio.create_task(
        DailyLeaderboard._calculate_daily_winners(leaderboard, date)
    )
    await asyncio.sleep(0)

    # The leaderboard should not consume the stats while waiting for the ranking.
    assert date in xp.DAILY_STATS

    producer = asyncio.create_task(produce_ranking())
    result = await winners_task
    await producer

    assert result["winners"]["msg"] == 111
    assert result["top3"]["msg"][0]["id"] == 111
    # Ranking cog consumed the stats after producing results.
    assert date not in xp.DAILY_STATS
