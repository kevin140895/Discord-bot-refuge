import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from utils.temp_vc_cleanup import delete_untracked_temp_vcs
import cogs.temp_vc as temp_vc


class DummyCategory:
    def __init__(self, voice_channels):
        self.voice_channels = voice_channels


@pytest.mark.asyncio
async def test_delete_untracked_skips_populated_channels():
    tracked = {1}
    ch1 = SimpleNamespace(id=1, name="PC", members=[], delete=AsyncMock())
    ch2 = SimpleNamespace(id=2, name="Console", members=[object()], delete=AsyncMock())
    ch3 = SimpleNamespace(id=3, name="Autre", members=[], delete=AsyncMock())
    ch4 = SimpleNamespace(id=4, name="Mobile", members=[], delete=AsyncMock())
    category = DummyCategory([ch1, ch2, ch3, ch4])
    bot = SimpleNamespace(get_channel=lambda cid: category)

    with patch("utils.temp_vc_cleanup.discord.CategoryChannel", DummyCategory):
        await delete_untracked_temp_vcs(bot, 123, tracked)

    ch1.delete.assert_not_awaited()
    ch2.delete.assert_not_awaited()
    ch3.delete.assert_not_awaited()
    ch4.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_repopulate_ids_when_missing(monkeypatch):
    temp_vc.TEMP_VC_IDS.clear()

    ch1 = SimpleNamespace(id=10, name="PC", members=[])
    ch2 = SimpleNamespace(id=20, name="General", members=[])
    category = DummyCategory([ch1, ch2])
    bot = SimpleNamespace(get_channel=lambda cid: category)

    saved = []
    def fake_save(ids):
        saved.append(set(ids))

    async def fake_save_cache(_cache, max_retries=3):
        pass

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        with patch("cogs.temp_vc.save_temp_vc_ids", fake_save):
            with patch("cogs.temp_vc.save_last_names_cache", fake_save_cache):
                with patch("cogs.temp_vc.discord.CategoryChannel", DummyCategory):
                    temp_vc.TempVCCog(bot)

    assert ch1.id in temp_vc.TEMP_VC_IDS
    assert ch2.id not in temp_vc.TEMP_VC_IDS
    assert saved and ch1.id in saved[0]


@pytest.mark.asyncio
async def test_rename_task_cancelled_on_channel_delete(monkeypatch):
    temp_vc.TEMP_VC_IDS.clear()
    temp_vc.TEMP_VC_IDS.add(42)

    loop = asyncio.get_running_loop()
    bot = SimpleNamespace(get_channel=lambda _cid: None, loop=loop)

    # Avoid starting real tasks and I/O
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
    assert task.cancelled()
    assert cog._rename_tasks.popped
