import os
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from cogs.machine_a_sous.machine_a_sous import MachineASousCog


@pytest.mark.asyncio
async def test_init_posts_closed_message(monkeypatch):
    monkeypatch.setattr(
        'cogs.machine_a_sous.machine_a_sous.is_open_now',
        lambda *_, **__: False,
    )

    bot = SimpleNamespace(
        wait_until_ready=AsyncMock(),
        loop=asyncio.get_event_loop(),
    )

    cog = MachineASousCog(bot)
    cog._ensure_poster_message = AsyncMock()
    post_mock = AsyncMock()
    cog.maintenance_loop.start = MagicMock()

    async def fake_ensure(opened: bool):
        await post_mock(opened)

    cog._ensure_state_message = AsyncMock(side_effect=fake_ensure)

    await cog._init_after_ready()

    post_mock.assert_awaited_once_with(False)
