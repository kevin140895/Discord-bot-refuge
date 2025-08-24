import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import cogs.temp_vc as temp_vc


@pytest.mark.asyncio
async def test_temp_channel_created_and_removed(monkeypatch):
    temp_vc.TEMP_VC_IDS.clear()

    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(get_channel=lambda _cid: None, loop=loop)

    # avoid starting real rename manager worker and file I/O
    monkeypatch.setattr(temp_vc.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(temp_vc, "save_temp_vc_ids", lambda ids: None)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    channel = SimpleNamespace(id=42, name="Temp", members=[], delete=AsyncMock())
    member = SimpleNamespace(id=1, move_to=AsyncMock())

    async def fake_create_temp_vc(_member):
        temp_vc.TEMP_VC_IDS.add(channel.id)
        temp_vc.save_temp_vc_ids(temp_vc.TEMP_VC_IDS)
        return channel

    monkeypatch.setattr(cog, "_create_temp_vc", fake_create_temp_vc)
    monkeypatch.setattr(cog, "_update_channel_name", AsyncMock())

    # simulate member joining lobby
    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=SimpleNamespace(id=temp_vc.LOBBY_VC_ID))
    await cog.on_voice_state_update(member, before, after)

    assert channel.id in temp_vc.TEMP_VC_IDS

    # simulate member leaving the temporary channel
    channel.members = []
    before = SimpleNamespace(channel=channel)
    after = SimpleNamespace(channel=None)
    await cog.on_voice_state_update(member, before, after)

    channel.delete.assert_awaited_once()
    assert channel.id not in temp_vc.TEMP_VC_IDS
