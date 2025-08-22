from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils.discord_utils import _fetch_channel_cached, _CHANNEL_CACHE


@pytest.mark.asyncio
async def test_fetch_channel_cached():
    dummy_channel = SimpleNamespace(id=123)
    bot = SimpleNamespace(fetch_channel=AsyncMock(return_value=dummy_channel))
    _CHANNEL_CACHE.clear()
    chan1 = await _fetch_channel_cached(bot, 123)
    chan2 = await _fetch_channel_cached(bot, 123)
    assert chan1 is chan2
    bot.fetch_channel.assert_awaited_once_with(123)
