from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import cogs.radio as radio_mod
from cogs.radio import RadioCog


def _radio_message(message_id: int):
    row = SimpleNamespace(
        children=[
            discord.ui.Button(custom_id="radio_rap_fr"),
            discord.ui.Button(custom_id="radio_rap"),
            discord.ui.Button(custom_id="radio_rock"),
            discord.ui.Button(custom_id="radio_hiphop"),
        ]
    )
    return SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(id=1),
        components=[row],
        edit=AsyncMock(),
        delete=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_ensure_radio_message_finds_message_within_recent_100():
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    set_radio_message = MagicMock()
    cog.store = SimpleNamespace(
        get_radio_message=lambda: None,
        set_radio_message=set_radio_message,
        clear_radio_message=MagicMock(),
    )

    recent = _radio_message(999)
    duplicate = _radio_message(998)
    non_bot = SimpleNamespace(author=SimpleNamespace(id=2), components=[])

    async def history(limit=None):
        assert limit == 100
        messages = [non_bot] * 60 + [recent, duplicate]
        for message in messages[:limit]:
            yield message

    channel = SimpleNamespace(id=123, history=history, send=AsyncMock())

    await cog._ensure_radio_message(channel)

    channel.send.assert_not_called()
    set_radio_message.assert_called_once_with(channel.id, recent.id)
    recent.edit.assert_awaited_once()
    recent.delete.assert_not_awaited()
    duplicate.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_radio_message_recreates_when_panel_is_beyond_recent_100(
    monkeypatch,
):
    class FakeNotFound(Exception):
        pass

    monkeypatch.setattr(radio_mod.discord, "NotFound", FakeNotFound)

    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    set_radio_message = MagicMock()
    cog.store = SimpleNamespace(
        get_radio_message=lambda: {"channel_id": "123", "message_id": "456"},
        set_radio_message=set_radio_message,
        clear_radio_message=MagicMock(),
    )

    old_panel = _radio_message(998)
    non_bot = SimpleNamespace(author=SimpleNamespace(id=2), components=[])
    all_messages = [non_bot] * 100 + [old_panel]

    async def history(limit=None):
        assert limit == 100
        for message in all_messages[:limit]:
            yield message

    fetch_message = AsyncMock(side_effect=FakeNotFound("stored panel missing"))
    new_panel = SimpleNamespace(id=1000)
    channel = SimpleNamespace(
        id=123,
        fetch_message=fetch_message,
        history=history,
        send=AsyncMock(return_value=new_panel),
    )

    await cog._ensure_radio_message(channel)

    fetch_message.assert_awaited_once_with(456)
    old_panel.edit.assert_not_awaited()
    old_panel.delete.assert_not_awaited()
    channel.send.assert_awaited_once()
    set_radio_message.assert_called_once_with(channel.id, new_panel.id)


@pytest.mark.asyncio
async def test_unexpected_stored_message_fetch_error_propagates():
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    cog.store = SimpleNamespace(
        get_radio_message=lambda: {"channel_id": "123", "message_id": "456"},
        set_radio_message=MagicMock(),
    )
    history = MagicMock()
    channel = SimpleNamespace(
        id=123,
        fetch_message=AsyncMock(side_effect=RuntimeError("radio fetch bug")),
        history=history,
        send=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="radio fetch bug"):
        await cog._ensure_radio_message(channel)

    history.assert_not_called()
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_panel_render_error_propagates():
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = RadioCog(bot)
    cog.store = SimpleNamespace(
        get_radio_message=lambda: {"channel_id": "123", "message_id": "456"},
        set_radio_message=MagicMock(),
    )
    message = _radio_message(456)
    message.edit = AsyncMock(side_effect=RuntimeError("radio render bug"))
    history = MagicMock()
    channel = SimpleNamespace(
        id=123,
        fetch_message=AsyncMock(return_value=message),
        history=history,
        send=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="radio render bug"):
        await cog._ensure_radio_message(channel)

    history.assert_not_called()
    channel.send.assert_not_awaited()
