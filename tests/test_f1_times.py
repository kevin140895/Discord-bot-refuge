import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.f1_standings import F1Standings


@pytest.mark.asyncio
async def test_parse_openf1_positions_returns_times():
    cog = F1Standings(MagicMock())
    assert cog._task is None
    cog._get_driver_info = AsyncMock(return_value={"name": "Driver", "team": "Team"})
    positions = [
        {"position": 1, "driver_number": 1, "time": 0},
        {"position": 2, "driver_number": 2, "best_lap_time": 83.123},
    ]
    results = await cog._parse_openf1_positions(positions)
    assert results[0]["time"] == "0"
    assert results[1]["time"] == "83.123"


@pytest.mark.asyncio
async def test_f1_monitor_starts_on_cog_load_and_stops_on_unload():
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.is_closed = MagicMock(return_value=False)

    cog = F1Standings(bot)
    cog._is_f1_weekend = AsyncMock(return_value=False)

    await cog.cog_load()
    await asyncio.sleep(0)

    task = cog._task
    assert task is not None
    assert not task.done()

    await cog.cog_unload()

    assert cog._task is None
    assert task.cancelled()
