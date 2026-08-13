from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import bot as bot_module
from utils.rename_manager import _RenameManager


def test_forget_channel_removes_history_and_is_idempotent():
    manager = _RenameManager()
    manager._last_per_channel[123] = 42.0
    manager._last_per_channel[456] = 84.0

    manager.forget_channel(123)
    manager.forget_channel(123)

    assert 123 not in manager._last_per_channel
    assert manager._last_per_channel == {456: 84.0}


@pytest.mark.asyncio
async def test_bot_forgets_history_when_discord_confirms_channel_deletion(monkeypatch):
    forget_channel = Mock()
    monkeypatch.setattr(bot_module.rename_manager, "forget_channel", forget_channel)

    channel = SimpleNamespace(id=987654321)

    await bot_module.RefugeBot.on_guild_channel_delete(object(), channel)

    forget_channel.assert_called_once_with(channel.id)
