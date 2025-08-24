import asyncio
import logging
from unittest import mock

import pytest
import discord

import config
from storage.xp_store import xp_store
from utils import level_feed


class DummyMessage:
    def __init__(self, *, content: str = "", embed: discord.Embed | None = None) -> None:
        self.content = content
        self.embed = embed

    async def edit(
        self, *, content: str | None = None, embed: discord.Embed | None = None
    ) -> None:
        if content is not None:
            self.content = content
        if embed is not None:
            self.embed = embed


class DummyChannel(discord.abc.Messageable):
    def __init__(self) -> None:
        self.sent: list[DummyMessage] = []

    async def send(
        self, content: str = "", *, embed: discord.Embed | None = None
    ) -> DummyMessage:  # type: ignore[override]
        msg = DummyMessage(content=content, embed=embed)
        self.sent.append(msg)
        return msg

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
    level_feed.router._pari_xp_messages.clear()

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
    assert chan.sent and chan.sent[0].embed
    assert chan.sent[0].embed.title == "🆙 Niveau augmenté !"


@pytest.mark.asyncio
async def test_level_down_pari_xp(setup_router):
    chan = setup_router
    uid = 2
    xp_store.data[str(uid)] = {"xp": 16900, "level": 13}
    await xp_store.add_xp(uid, -2500, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert chan.sent and chan.sent[0].embed
    assert chan.sent[0].embed.title == "⬇️ Niveau diminué"


@pytest.mark.asyncio
async def test_level_up_machine_a_sous(setup_router):
    chan = setup_router
    uid = 3
    xp_store.data[str(uid)] = {"xp": 14400, "level": 12}
    await xp_store.add_xp(uid, 2500, guild_id=1, source="machine_a_sous")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert chan.sent and "🎰" in chan.sent[0].content


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
    assert chan.sent[0].embed and "niv. 14" in chan.sent[0].embed.description


@pytest.mark.asyncio
async def test_edit_message_on_repeated_level_up(setup_router):
    chan = setup_router
    uid = 6
    xp_store.data[str(uid)] = {"xp": 14400, "level": 12}
    await xp_store.add_xp(uid, 2500, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(chan.sent) == 1
    msg = chan.sent[0]
    assert msg.embed and "niv. 13" in msg.embed.description
    await xp_store.add_xp(uid, 2700, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(chan.sent) == 1
    assert chan.sent[0] is msg
    assert msg.embed and "niv. 14" in msg.embed.description


@pytest.mark.asyncio
async def test_edit_message_on_repeated_level_down(setup_router):
    chan = setup_router
    uid = 7
    xp_store.data[str(uid)] = {"xp": 16900, "level": 13}
    await xp_store.add_xp(uid, -2000, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(chan.sent) == 1
    msg = chan.sent[0]
    assert msg.embed and "niv. 12" in msg.embed.description
    await xp_store.add_xp(uid, -2000, guild_id=1, source="pari_xp")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(chan.sent) == 1
    assert chan.sent[0] is msg
    assert msg.embed and "niv. 11" in msg.embed.description


@pytest.mark.asyncio
async def test_missing_permissions(monkeypatch, caplog):
    class ForbiddenChannel(DummyChannel):
        async def send(
            self, content: str = "", *, embed: discord.Embed | None = None
        ) -> DummyMessage:
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
