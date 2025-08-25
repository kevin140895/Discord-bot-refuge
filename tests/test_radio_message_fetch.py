from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.radio import RadioCog


@pytest.mark.asyncio
async def test_ensure_radio_message_uses_stored_id():
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    channel = SimpleNamespace(id=123)
    cog.store.set_radio_message(str(channel.id), "456")

    btn = discord.ui.Button(custom_id="radio_hiphop")
    row = SimpleNamespace(children=[btn])
    msg = SimpleNamespace(components=[row])

    channel.fetch_message = AsyncMock(return_value=msg)
    channel.history = AsyncMock()
    channel.send = AsyncMock()

    await cog._ensure_radio_message(channel)

    channel.fetch_message.assert_awaited_once_with(456)
    channel.history.assert_not_called()
    channel.send.assert_not_called()
