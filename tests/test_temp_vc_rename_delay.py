import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import cogs.temp_vc as temp_vc
from config import RENAME_DELAY


@pytest.mark.asyncio
async def test_rename_channel_uses_config_delay(monkeypatch):
    channel = SimpleNamespace(id=1, name="Old", members=[object()])
    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(loop=loop)

    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(temp_vc.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(temp_vc, "safe_channel_edit", AsyncMock())

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    cog._compute_channel_name = lambda ch: "New"

    task = loop.create_task(cog._rename_channel(channel))
    cog._rename_tasks[channel.id] = task
    await task

    assert delays == [RENAME_DELAY]
    temp_vc.safe_channel_edit.assert_awaited_once_with(channel, name="New")
