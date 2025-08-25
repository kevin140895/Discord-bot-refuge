from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.radio import RadioCog


@pytest.mark.asyncio
async def test_ensure_radio_message_uses_stored_id():
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    store = SimpleNamespace(
        get_radio_message=lambda: {"channel_id": "123", "message_id": "456"},
        set_radio_message=MagicMock(),
        clear_radio_message=MagicMock(),
    )
    cog.store = store
    channel = SimpleNamespace(id=123)

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


@pytest.mark.asyncio
async def test_ensure_radio_message_creates_message_with_buttons():
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    set_radio_message = MagicMock()
    store = SimpleNamespace(
        get_radio_message=lambda: None,
        set_radio_message=set_radio_message,
        clear_radio_message=MagicMock(),
    )
    cog.store = store

    async def empty_history(limit: int = 50):
        if False:
            yield None

    channel = SimpleNamespace(
        id=123,
        history=empty_history,
        send=AsyncMock(return_value=SimpleNamespace(id=456)),
    )

    await cog._ensure_radio_message(channel)

    channel.send.assert_awaited_once()
    set_radio_message.assert_called_once_with(str(channel.id), "456")
    view = channel.send.await_args.kwargs["view"]
    assert any(
        getattr(child, "custom_id", None) == "radio_hiphop" for child in view.children
    )
