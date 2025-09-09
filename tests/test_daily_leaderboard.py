import asyncio
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import cogs.xp as xp
import cogs.daily_leaderboard as dl
from cogs.daily_leaderboard import DailyLeaderboard


@pytest.mark.asyncio
async def test_calculate_daily_winners(monkeypatch):
    date = "2024-01-01"
    xp.DAILY_STATS = {
        date: {
            "1": {"messages": 5, "voice": 60},
            "2": {"messages": 3, "voice": 120},
        }
    }
    dl.DAILY_STATS = xp.DAILY_STATS
    xp.DAILY_LOCK = asyncio.Lock()
    dl.DAILY_LOCK = xp.DAILY_LOCK

    async def dummy_save() -> None:
        return None

    monkeypatch.setattr(xp, "save_daily_stats_to_disk", dummy_save)
    cog = DailyLeaderboard.__new__(DailyLeaderboard)
    result = await DailyLeaderboard._calculate_daily_winners(cog, date)
    assert result["winners"] == {"msg": 1, "vc": 2, "mvp": 1}
    assert date not in xp.DAILY_STATS
