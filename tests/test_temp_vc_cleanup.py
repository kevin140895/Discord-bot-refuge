import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from storage.temp_vc_store import GENERIC_TEMP_VC_TYPE, build_temp_vc_record
from utils.temp_vc_cleanup import delete_empty_managed_temp_vcs
import cogs.temp_vc as temp_vc


class DummyVoiceChannel:
    def __init__(
        self,
        channel_id,
        name,
        *,
        members=None,
        category_id=None,
        created_at=None,
    ):
        self.id = channel_id
        self.name = name
        self.members = list(members or [])
        self.category_id = category_id
        self.created_at = created_at or datetime.now(timezone.utc)
        self.delete = AsyncMock()


@pytest.mark.asyncio
async def test_cleanup_only_deletes_empty_registered_channels():
    registered_empty = DummyVoiceChannel(1, "PC")
    registered_populated = DummyVoiceChannel(2, "Console", members=[object()])
    permanent_chat = DummyVoiceChannel(3, "Chat")
    permanent_mobile = DummyVoiceChannel(4, "Mobile • Jeu")

    channels = {
        channel.id: channel
        for channel in (
            registered_empty,
            registered_populated,
            permanent_chat,
            permanent_mobile,
        )
    }
    bot = SimpleNamespace(get_channel=channels.get)
    records = {
        1: build_temp_vc_record(1, 101, "2026-08-13T00:00:00+00:00"),
        2: build_temp_vc_record(2, 102, "2026-08-13T00:00:01+00:00"),
    }

    with patch("utils.temp_vc_cleanup.discord.VoiceChannel", DummyVoiceChannel):
        deleted = await delete_empty_managed_temp_vcs(bot, records)

    assert deleted == {1}
    registered_empty.delete.assert_awaited_once()
    registered_populated.delete.assert_not_awaited()
    permanent_chat.delete.assert_not_awaited()
    permanent_mobile.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_rejects_incomplete_or_wrong_type_provenance():
    incomplete = DummyVoiceChannel(10, "Chat")
    wrong_type = DummyVoiceChannel(11, "PC")
    bot = SimpleNamespace(get_channel={10: incomplete, 11: wrong_type}.get)
    records = {
        10: {
            "channel_id": 10,
            "owner_id": 0,
            "created_at": "",
            "type": GENERIC_TEMP_VC_TYPE,
        },
        11: build_temp_vc_record(
            11,
            111,
            "2026-08-13T00:00:00+00:00",
            record_type="streamer",
        ),
    }

    with patch("utils.temp_vc_cleanup.discord.VoiceChannel", DummyVoiceChannel):
        deleted = await delete_empty_managed_temp_vcs(bot, records)

    assert deleted == set()
    incomplete.delete.assert_not_awaited()
    wrong_type.delete.assert_not_awaited()


def test_constructor_never_adopts_channels_from_name_or_category():
    temp_vc.TEMP_VC_IDS.clear()
    temp_vc.TEMP_VC_REGISTRY.clear()

    permanent_chat = DummyVoiceChannel(
        20,
        "Chat",
        category_id=temp_vc.TEMP_VC_CATEGORY,
    )
    category = SimpleNamespace(voice_channels=[permanent_chat])
    bot = SimpleNamespace(get_channel=lambda _cid: category)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        temp_vc.TempVCCog(bot)

    assert permanent_chat.id not in temp_vc.TEMP_VC_IDS
    assert permanent_chat.id not in temp_vc.TEMP_VC_REGISTRY


@pytest.mark.asyncio
async def test_legacy_migration_requires_persisted_owner_and_ignores_name(monkeypatch):
    temp_vc.TEMP_VC_IDS.clear()
    temp_vc.TEMP_VC_REGISTRY.clear()

    safe = DummyVoiceChannel(
        30,
        "Salon renommé manuellement",
        category_id=temp_vc.TEMP_VC_CATEGORY,
    )
    no_owner = DummyVoiceChannel(
        31,
        "Chat",
        category_id=temp_vc.TEMP_VC_CATEGORY,
    )
    channels = {30: safe, 31: no_owner}
    bot = SimpleNamespace(get_channel=channels.get)

    monkeypatch.setattr(temp_vc, "load_temp_vc_ids", lambda: {30, 31})
    monkeypatch.setattr(temp_vc, "load_temp_vc_owners", lambda: {30: 300})
    save_registry = AsyncMock()
    monkeypatch.setattr(temp_vc, "save_temp_vc_registry_async", save_registry)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    with patch("cogs.temp_vc.discord.VoiceChannel", DummyVoiceChannel):
        await cog._migrate_legacy_temp_vcs()

    assert 30 in temp_vc.TEMP_VC_IDS
    assert temp_vc.TEMP_VC_REGISTRY[30]["owner_id"] == 300
    assert temp_vc.TEMP_VC_REGISTRY[30]["type"] == GENERIC_TEMP_VC_TYPE
    assert temp_vc.TEMP_VC_REGISTRY[30]["created_at"] == safe.created_at.isoformat()
    assert 31 not in temp_vc.TEMP_VC_IDS
    assert 31 not in temp_vc.TEMP_VC_REGISTRY
    save_registry.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_task_cancelled_on_channel_delete(monkeypatch):
    temp_vc.TEMP_VC_IDS.clear()
    temp_vc.TEMP_VC_REGISTRY.clear()
    temp_vc.TEMP_VC_IDS.add(42)
    temp_vc.TEMP_VC_REGISTRY[42] = build_temp_vc_record(
        42,
        1,
        "2026-08-13T00:00:00+00:00",
    )

    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(get_channel=lambda _cid: None, loop=loop)

    # Avoid starting real tasks and I/O
    monkeypatch.setattr(temp_vc.rename_manager, "start", AsyncMock())

    async def no_save_registry(records, max_retries=3):
        return None

    async def no_save_cache(cache, max_retries=3):
        return None

    monkeypatch.setattr(temp_vc, "save_temp_vc_registry_async", no_save_registry)
    monkeypatch.setattr(temp_vc, "save_last_names_cache", no_save_cache)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    channel = SimpleNamespace(id=42, name="Temp", members=[], delete=AsyncMock())
    member = SimpleNamespace(id=1)

    # Create a dummy rename task and track pop calls
    class TrackingDict(dict):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.popped = False

        def pop(self, key, default=None):
            self.popped = True
            return super().pop(key, default)

    task = loop.create_task(asyncio.sleep(3600))
    cog._rename_tasks = TrackingDict({channel.id: task})

    before = SimpleNamespace(channel=channel)
    after = SimpleNamespace(channel=None)

    await cog.on_voice_state_update(member, before, after)
    await asyncio.sleep(0)

    channel.delete.assert_awaited_once()
    assert channel.id not in cog._rename_tasks
    assert channel.id not in temp_vc.TEMP_VC_REGISTRY
    assert task.cancelled()
    assert cog._rename_tasks.popped
