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

    async def no_save_ids(ids, max_retries=3):
        return None

    async def no_save_cache(cache, max_retries=3):
        return None

    monkeypatch.setattr(temp_vc, "save_temp_vc_ids_async", no_save_ids)
    monkeypatch.setattr(temp_vc, "save_last_names_cache", no_save_cache)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    channel = SimpleNamespace(id=42, name="Temp", members=[], delete=AsyncMock())
    member = SimpleNamespace(id=1, move_to=AsyncMock())

    async def fake_create_temp_vc(_member):
        temp_vc.TEMP_VC_IDS.add(channel.id)
        await temp_vc.save_temp_vc_ids_async(temp_vc.TEMP_VC_IDS)
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


@pytest.mark.asyncio
async def test_streamer_channel_overwrites(monkeypatch):
    temp_vc.TEMP_VC_IDS.clear()

    class DummyCategory:
        pass

    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(
        get_channel=lambda _cid: None,
        loop=loop,
        user=SimpleNamespace(id=999),
    )

    monkeypatch.setattr(temp_vc.rename_manager, "start", AsyncMock())

    async def no_save_ids(ids, max_retries=3):
        return None

    async def no_save_cache(cache, max_retries=3):
        return None

    monkeypatch.setattr(temp_vc, "save_temp_vc_ids_async", no_save_ids)
    monkeypatch.setattr(temp_vc, "save_last_names_cache", no_save_cache)
    monkeypatch.setattr(temp_vc.discord, "CategoryChannel", DummyCategory)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    category = DummyCategory()
    trigger_channel = SimpleNamespace(category=category)
    default_role = SimpleNamespace(id=111)
    streamer_role = SimpleNamespace(id=temp_vc.STREAMER_ALLOWED_ROLE_ID)
    bot_member = SimpleNamespace(id=999)

    async def fake_create_voice_channel(name, *, category=None, user_limit=None, overwrites=None):
        return SimpleNamespace(id=555, name=name, overwrites=overwrites)

    guild = SimpleNamespace(
        default_role=default_role,
        get_role=lambda rid: streamer_role if rid == temp_vc.STREAMER_ALLOWED_ROLE_ID else None,
        get_member=lambda mid: bot_member if mid == bot.user.id else None,
        create_voice_channel=AsyncMock(side_effect=fake_create_voice_channel),
    )
    member = SimpleNamespace(id=123, guild=guild, roles=[streamer_role])

    channel = await cog._create_streamer_vc(member, trigger_channel)

    guild.create_voice_channel.assert_awaited_once()
    overwrites = channel.overwrites
    assert default_role in overwrites
    assert streamer_role in overwrites
    assert bot_member in overwrites
    assert overwrites[default_role].view_channel is False
    assert overwrites[default_role].connect is False
    assert overwrites[streamer_role].view_channel is True
    assert overwrites[streamer_role].connect is True
    assert overwrites[streamer_role].speak is True
    assert overwrites[bot_member].manage_channels is True
