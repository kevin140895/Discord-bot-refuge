import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import pytest

import cogs.temp_vc as temp_vc


@pytest.mark.asyncio
async def test_long_game_name_truncated(monkeypatch):
    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(get_channel=lambda _id: None, loop=loop)
    monkeypatch.setattr(temp_vc.rename_manager, "start", AsyncMock())

    async def no_save_ids(ids, max_retries=3):
        return None

    async def no_save_cache(cache, max_retries=3):
        return None

    monkeypatch.setattr(temp_vc, "save_temp_vc_ids_async", no_save_ids)
    monkeypatch.setattr(temp_vc, "save_last_names_cache", no_save_cache)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    long_name = "Y" * 200
    player = SimpleNamespace(activities=[discord.Game(long_name)], voice=SimpleNamespace(self_mute=False))
    channel = SimpleNamespace(members=[player])

    cog._base_name_from_members = lambda members: "PC"

    name = cog._compute_channel_name(channel)
    expected_len = 100 - len("PC • ")
    assert len(name) == 100
    assert name == "PC • " + long_name[:expected_len]
