import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.f1_standings import F1Standings


@pytest.mark.asyncio
async def test_parse_openf1_positions_returns_times(monkeypatch):
    monkeypatch.setattr(asyncio, "create_task", lambda *args, **kwargs: MagicMock())
    cog = F1Standings(MagicMock())
    cog._get_driver_info = AsyncMock(return_value={"name": "Driver", "team": "Team"})
    positions = [
        {"position": 1, "driver_number": 1, "time": 0},
        {"position": 2, "driver_number": 2, "best_lap_time": 83.123},
    ]
    results = await cog._parse_openf1_positions(positions)
    assert results[0]["time"] == "0"
    assert results[1]["time"] == "83.123"
