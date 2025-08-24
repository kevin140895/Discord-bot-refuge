import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import pytest

import cogs.temp_vc as temp_vc


@pytest.mark.asyncio
async def test_channel_name_truncated(monkeypatch):
    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(get_channel=lambda _id: None, loop=loop)
    monkeypatch.setattr(temp_vc.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(temp_vc, "save_temp_vc_ids", lambda ids: None)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    long_name = "X" * 150
    player = SimpleNamespace(activities=[discord.Game(long_name)], voice=SimpleNamespace(self_mute=False))
    channel = SimpleNamespace(members=[player])

    cog._base_name_from_members = lambda members: "Base"

    name = cog._compute_channel_name(channel)
    assert len(name) == 100
    assert name == "Base • " + long_name[:93]

