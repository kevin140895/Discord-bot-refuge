import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.rock_radio import RockRadioCog


@pytest.mark.asyncio
async def test_cog_load_connects_when_bot_ready():
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), is_ready=lambda: True)
    cog = RockRadioCog(bot)
    cog._connect_and_play = AsyncMock()
    await cog.cog_load()
    cog._connect_and_play.assert_awaited_once()


@pytest.mark.asyncio
async def test_cog_load_skips_when_bot_not_ready():
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), is_ready=lambda: False)
    cog = RockRadioCog(bot)
    cog._connect_and_play = AsyncMock()
    await cog.cog_load()
    cog._connect_and_play.assert_not_called()
