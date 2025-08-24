import asyncio
import logging
from unittest import mock

import pytest
import discord

import config
from storage.xp_store import xp_store
from utils import level_feed


class DummyChannel(discord.abc.Messageable):
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str) -> None:  # type: ignore[override]
        self.sent.append(content)

    async def _get_channel(self):  # pragma: no cover - required by abc
        return self


class DummyBot:
    def __init__(self, channel: DummyChannel) -> None:
        self._channel = channel

    def get_channel(self, cid: int):
        return self._channel if cid == config.LEVEL_FEED_CHANNEL_ID else None

    async def fetch_channel(self, cid: int):
        return self.get_channel(cid)

    def get_user(self, uid: int):
        class U:
            def __init__(self, uid: int) -> None:
                self.id = uid
                self.mention = f"<@{uid}>"

        return U(uid)


@pytest.fixture(autouse=True)
def setup_router(monkeypatch):
    chan = DummyChannel()
    bot = DummyBot(chan)
    level_feed.router.setup(bot)

    async def fast_dispatch(key):
        await asyncio.sleep(0)
        event = level_feed.router._pending.pop(key, None)
        level_feed.router._tasks.pop(key, None)
        if event:
            await level_feed.router._handle(event)

    monkeypatch.setattr(level_feed.router, "_dispatch_later", fast_dispatch)
    monkeypatch.setattr(config, "ENABLE_GAME_LEVEL_FEED", True)
    xp_store.data.clear()
    xp_store.lock = asyncio.Lock()
    return chan


@pytest.mark.asyncio
async def test_level_up_pari_xp(setup_router):
    chan = setup_router
    uid = 1
    xp_store.data[str(uid)] = {"xp": 14400, "level": 12}
    await xp_store.add_xp(uid, 2500, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert chan.sent and "🤑" in chan.sent[0]


@pytest.mark.asyncio
async def test_level_down_pari_xp(setup_router):
    chan = setup_router
    uid = 2
    xp_store.data[str(uid)] = {"xp": 16900, "level": 13}
    await xp_store.add_xp(uid, -2500, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert chan.sent and chan.sent[0].startswith("⬇️")


@pytest.mark.asyncio
async def test_level_up_machine_a_sous(setup_router):
    chan = setup_router
    uid = 3
    xp_store.data[str(uid)] = {"xp": 14400, "level": 12}
    await xp_store.add_xp(uid, 2500, guild_id=1, source="machine_a_sous")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert chan.sent and "🎰" in chan.sent[0]


@pytest.mark.asyncio
async def test_no_message_without_level_change(setup_router):
    chan = setup_router
    uid = 4
    xp_store.data[str(uid)] = {"xp": 14400, "level": 12}
    await xp_store.add_xp(uid, 10, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert chan.sent == []


@pytest.mark.asyncio
async def test_antispam_coalesce(setup_router):
    chan = setup_router
    uid = 5
    xp_store.data[str(uid)] = {"xp": 14400, "level": 12}
    await xp_store.add_xp(uid, 2500, guild_id=1, source="pari_xp")
    await xp_store.add_xp(uid, 2700, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(chan.sent) == 1
    assert "niv. 14" in chan.sent[0]


@pytest.mark.asyncio
async def test_missing_permissions(monkeypatch, caplog):
    class ForbiddenChannel(DummyChannel):
        async def send(self, content: str) -> None:
            raise discord.Forbidden(mock.Mock(), "no perms")

    chan = ForbiddenChannel()
    bot = DummyBot(chan)
    level_feed.router.setup(bot)

    async def fast_dispatch(key):
        await asyncio.sleep(0)
        event = level_feed.router._pending.pop(key, None)
        level_feed.router._tasks.pop(key, None)
        if event:
            await level_feed.router._handle(event)

    monkeypatch.setattr(level_feed.router, "_dispatch_later", fast_dispatch)
    xp_store.data.clear()
    xp_store.lock = asyncio.Lock()
    xp_store.data["6"] = {"xp": 14400, "level": 12}
    with caplog.at_level(logging.WARNING):
        await xp_store.add_xp(6, 2500, guild_id=1, source="pari_xp")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert chan.sent == []
    assert any("permission" in r.message for r in caplog.records)
