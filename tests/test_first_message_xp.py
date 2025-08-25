import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import cogs.first_message as fm


@pytest.mark.asyncio
async def test_first_message_awards_xp_once(tmp_path, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 1, 1, 9, 0, tzinfo=tz)

    monkeypatch.setattr(fm, "datetime", FixedDatetime)
    monkeypatch.setattr(fm, "FIRST_WIN_FILE", str(tmp_path / "first_win.json"))
    monkeypatch.setattr(fm.FirstMessageCog, "_save_state", AsyncMock())

    add_xp = AsyncMock(return_value=(0, 0, 0, 0))
    monkeypatch.setattr(fm.xp_store, "add_xp", add_xp)

    async def wait_until_ready():
        return None

    bot = SimpleNamespace(announce_level_up=AsyncMock(), wait_until_ready=wait_until_ready)
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=1, mention="@user"),
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(send=AsyncMock()),
    )

    with patch.object(fm.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = fm.FirstMessageCog(bot)

    # Ensure first message grants XP
    cog.first_message_claimed = False
    await cog.on_message(message)
    await cog.on_message(message)  # second call should not add XP

    add_xp.assert_awaited_once_with(1, 400, guild_id=123, source="message")
    assert message.channel.send.await_count == 1
    assert cog.first_message_claimed is True
