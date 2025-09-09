from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import cogs.daily_ranking as daily_ranking
import cogs.daily_awards as daily_awards
import cogs.xp as xp
from cogs.daily_ranking import DailyRankingAndRoles, PARIS_TZ


@pytest.mark.asyncio
async def test_startup_recovers_and_awards(tmp_path):
    rank_file = tmp_path / "daily_ranking.json"
    daily_ranking.DAILY_RANK_FILE = str(rank_file)
    daily_awards.DAILY_RANK_FILE = str(rank_file)

    xp.DAILY_STATS.clear()
    yesterday = (datetime.now(PARIS_TZ) - timedelta(days=1)).date().isoformat()
    xp.DAILY_STATS[yesterday] = {"1": {"messages": 5}, "2": {"messages": 3}}

    bot = SimpleNamespace(wait_until_ready=AsyncMock())
    cog = DailyRankingAndRoles.__new__(DailyRankingAndRoles)
    cog.bot = bot
    with patch("cogs.xp.save_daily_stats_to_disk", new_callable=AsyncMock):
        await DailyRankingAndRoles._startup_check(cog)

    data = daily_ranking.read_json_safe(str(rank_file))
    assert data["date"] == yesterday

    channel = SimpleNamespace(send=AsyncMock())
    award_bot = SimpleNamespace(get_channel=lambda _cid: channel, wait_until_ready=AsyncMock())
    award_cog = daily_awards.DailyAwards.__new__(daily_awards.DailyAwards)
    award_cog.bot = award_bot
    award_cog._read_state = lambda: {}
    award_cog._write_state = lambda data: None
    award_cog._build_message = AsyncMock(return_value="msg")
    award_cog._get_announce_channel = AsyncMock(return_value=channel)
    await daily_awards.DailyAwards._startup_check(award_cog)
    channel.send.assert_awaited_once_with("msg")

