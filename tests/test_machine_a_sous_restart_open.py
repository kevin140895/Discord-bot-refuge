import os
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

import pytest
import discord

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from cogs.machine_a_sous.machine_a_sous import MachineASousCog


@pytest.mark.asyncio
async def test_restart_open_replaces_closed_poster(monkeypatch):
    monkeypatch.setattr(
        "cogs.machine_a_sous.machine_a_sous.is_open_now", lambda *_, **__: True
    )

    channel = AsyncMock(spec=discord.TextChannel)
    channel.id = 123
    channel.fetch_message = AsyncMock(return_value=SimpleNamespace(components=[]))

    get_channel_mock = MagicMock(return_value=channel)
    bot = SimpleNamespace(
        get_channel=get_channel_mock,
        wait_until_ready=AsyncMock(),
        loop=asyncio.get_event_loop(),
    )

    cog = MachineASousCog(bot)
    monkeypatch.setattr(
        cog.store,
        "get_poster",
        lambda: {"channel_id": str(channel.id), "message_id": "456"},
    )
    monkeypatch.setattr(cog.store, "set_poster", lambda *args, **kwargs: None)
    cog._replace_poster_message = AsyncMock()
    cog._ensure_state_message = AsyncMock()
    cog.maintenance_loop.start = MagicMock()

    await cog._init_after_ready()

    cog._replace_poster_message.assert_awaited_once()
