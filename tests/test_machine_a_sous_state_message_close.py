import discord
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.machine_a_sous.machine_a_sous import (
    MachineASousCog,
    NOTIF_ROLE_ID,
    GENERAL_CHANNEL_ID,
)


@pytest.mark.asyncio
async def test_post_state_message_mentions_role_when_closed(monkeypatch):
    async def empty_history(limit=20):
        if False:
            yield None

    channel = AsyncMock(spec=discord.TextChannel)
    channel.id = 123
    channel.send = AsyncMock(return_value=SimpleNamespace(id=456))
    channel.history = lambda limit=20: empty_history(limit)

    other_channel_id = 123
    monkeypatch.setattr(
        "cogs.machine_a_sous.machine_a_sous.ANNOUNCE_CHANNEL_ID", other_channel_id
    )
    get_channel_mock = MagicMock(return_value=channel)
    bot = SimpleNamespace(
        get_channel=get_channel_mock,
        user=SimpleNamespace(id=999),
    )

    cog = MachineASousCog(bot)
    monkeypatch.setattr(cog.store, "get_state_message", lambda: None)
    monkeypatch.setattr(cog.store, "set_state_message", lambda *args: None)

    await cog._post_state_message(False)

    get_channel_mock.assert_called_once_with(other_channel_id)
    channel.send.assert_awaited_once()
    _, kwargs = channel.send.await_args
    assert f"<@&{NOTIF_ROLE_ID}>" in kwargs["content"]
    assert isinstance(kwargs["embed"], discord.Embed)
    assert kwargs["allowed_mentions"].roles


@pytest.mark.asyncio
@pytest.mark.parametrize("opened", [True, False])
async def test_open_close_announcements_never_post_in_general(opened, monkeypatch):
    monkeypatch.setattr(
        "cogs.machine_a_sous.machine_a_sous.ANNOUNCE_CHANNEL_ID",
        GENERAL_CHANNEL_ID,
    )
    bot = SimpleNamespace(get_channel=MagicMock())
    cog = MachineASousCog(bot)

    await cog._post_state_message(opened)
    await cog._ensure_state_message(opened)

    bot.get_channel.assert_not_called()
