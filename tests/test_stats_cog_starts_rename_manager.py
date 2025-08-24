from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs import stats


@pytest.mark.asyncio
async def test_stats_cog_starts_rename_manager(monkeypatch):
    bot = SimpleNamespace(
        add_cog=AsyncMock(),
        wait_until_ready=AsyncMock(),
        guilds=[]
    )
    start_mock = AsyncMock()
    monkeypatch.setattr("cogs.stats.rename_manager.start", start_mock)

    await stats.setup(bot)

    start_mock.assert_awaited_once()

    cog = bot.add_cog.call_args.args[0]
    cog.refresh_members.cancel()
    cog.refresh_online.cancel()
    cog.refresh_voice.cancel()
