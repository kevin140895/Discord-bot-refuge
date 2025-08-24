import discord
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.machine_a_sous.machine_a_sous import (
    MachineASousCog,
    NOTIF_ROLE_ID,
    CHANNEL_ID,
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

    get_channel_mock = MagicMock(return_value=channel)
    bot = SimpleNamespace(
        get_channel=get_channel_mock,
        user=SimpleNamespace(id=999),
    )

    cog = MachineASousCog(bot)
    monkeypatch.setattr(cog.store, "get_state_message", lambda: None)
    monkeypatch.setattr(cog.store, "set_state_message", lambda *args: None)

    await cog._post_state_message(False)

    get_channel_mock.assert_called_once_with(CHANNEL_ID)
    channel.send.assert_awaited_once()
    _, kwargs = channel.send.await_args
    assert f"<@&{NOTIF_ROLE_ID}>" in kwargs["content"]
    assert isinstance(kwargs["embed"], discord.Embed)
    assert kwargs["allowed_mentions"].roles
