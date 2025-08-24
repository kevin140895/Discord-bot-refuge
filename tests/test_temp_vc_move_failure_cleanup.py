import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import discord

import cogs.temp_vc as temp_vc


@pytest.mark.asyncio
async def test_temp_channel_deleted_on_move_failure(monkeypatch):
    temp_vc.TEMP_VC_IDS.clear()

    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(get_channel=lambda _cid: None, loop=loop)

    # avoid starting real rename manager worker and file I/O
    monkeypatch.setattr(temp_vc.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(temp_vc, "save_temp_vc_ids", lambda ids: None)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    channel = SimpleNamespace(id=42, name="Temp", members=[], delete=AsyncMock())
    http_exc = discord.HTTPException(SimpleNamespace(status=403, reason="forbidden"), "forbidden")
    member = SimpleNamespace(id=1, move_to=AsyncMock(side_effect=http_exc))

    async def fake_create_temp_vc(_member):
        temp_vc.TEMP_VC_IDS.add(channel.id)
        temp_vc.save_temp_vc_ids(temp_vc.TEMP_VC_IDS)
        cog._last_names[channel.id] = channel.name
        return channel

    monkeypatch.setattr(cog, "_create_temp_vc", fake_create_temp_vc)
    update_mock = AsyncMock()
    monkeypatch.setattr(cog, "_update_channel_name", update_mock)

    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=SimpleNamespace(id=temp_vc.LOBBY_VC_ID))
    await cog.on_voice_state_update(member, before, after)

    channel.delete.assert_awaited_once()
    assert channel.id not in temp_vc.TEMP_VC_IDS
    update_mock.assert_not_awaited()
