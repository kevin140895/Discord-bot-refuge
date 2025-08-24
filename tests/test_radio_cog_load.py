import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.radio import RadioCog


@pytest.mark.asyncio
async def test_cog_load_connects_when_bot_ready(monkeypatch):
    bot = SimpleNamespace(
        loop=asyncio.get_event_loop(),
        is_ready=lambda: True,
        get_channel=lambda cid: object(),
    )
    cog = RadioCog(bot)
    cog._connect_and_play = AsyncMock()
    cog._ensure_radio_message = AsyncMock()
    import cogs.radio as radio_mod
    monkeypatch.setattr(radio_mod.discord.abc, "Messageable", object)
    await cog.cog_load()
    cog._connect_and_play.assert_awaited_once()
    cog._ensure_radio_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_cog_load_skips_when_bot_not_ready():
    bot = SimpleNamespace(loop=asyncio.get_event_loop(), is_ready=lambda: False)
    cog = RadioCog(bot)
    cog._connect_and_play = AsyncMock()
    cog._ensure_radio_message = AsyncMock()
    await cog.cog_load()
    cog._connect_and_play.assert_not_called()
    cog._ensure_radio_message.assert_not_called()
