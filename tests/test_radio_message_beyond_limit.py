from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.radio import RadioCog


@pytest.mark.asyncio
async def test_ensure_radio_message_finds_message_beyond_limit():
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    set_radio_message = MagicMock()
    store = SimpleNamespace(
        get_radio_message=lambda: None,
        set_radio_message=set_radio_message,
        clear_radio_message=MagicMock(),
    )
    cog.store = store

    row = SimpleNamespace(
        children=[
            discord.ui.Button(custom_id="radio_rap_fr"),
            discord.ui.Button(custom_id="radio_rap"),
            discord.ui.Button(custom_id="radio_rock"),
            discord.ui.Button(custom_id="radio_hiphop"),
        ]
    )
    recent = SimpleNamespace(
        id=999,
        author=SimpleNamespace(id=1),
        components=[row],
        edit=AsyncMock(),
        delete=AsyncMock(),
    )
    old = SimpleNamespace(
        id=998,
        author=SimpleNamespace(id=1),
        components=[row],
        edit=AsyncMock(),
        delete=AsyncMock(),
    )
    non_bot = SimpleNamespace(author=SimpleNamespace(id=2), components=[])

    async def history(limit=None):
        assert limit is None
        for _ in range(60):
            yield non_bot
        yield recent
        yield old

    channel = SimpleNamespace(id=123, history=history, send=AsyncMock())

    await cog._ensure_radio_message(channel)

    channel.send.assert_not_called()
    set_radio_message.assert_called_once_with(channel.id, recent.id)
    recent.edit.assert_awaited_once()
    recent.delete.assert_not_awaited()
    old.delete.assert_awaited_once()
