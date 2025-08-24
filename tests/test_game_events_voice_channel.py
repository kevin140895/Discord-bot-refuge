import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import discord

# Ensure project root is on sys.path when running this test in isolation
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import cogs.game_events as game_cog
from utils.game_events import GameEvent, EVENTS


@pytest.mark.asyncio
async def test_voice_channel_created_when_positive_rsvp(monkeypatch):
    EVENTS.clear()
    now = datetime.now(timezone.utc)
    evt = GameEvent(
        id="e1",
        guild_id=1,
        creator_id=42,
        game_type="FPS",
        game_name="Apex",
        time=now + timedelta(minutes=5),
        channel_id=123,
        message_id=456,
    )
    evt.rsvps = {"1": "yes"}

    member_creator = SimpleNamespace(id=42, display_name="Alice", send=AsyncMock())
    member_yes = SimpleNamespace(id=1, display_name="Bob", send=AsyncMock())
    members = {42: member_creator, 1: member_yes}
    vc = SimpleNamespace(id=999, members=[], mention="<#999>")

    guild = SimpleNamespace(
        get_member=lambda uid: members.get(uid),
        create_voice_channel=AsyncMock(return_value=vc),
        get_channel=lambda cid: None,
    )
    bot = SimpleNamespace(get_guild=lambda gid: guild, add_view=lambda *a, **k: None)

    with patch.object(game_cog.tasks.Loop, "start", lambda self, *a, **k: None):
        with patch("cogs.game_events.load_events", lambda: None):
            cog = game_cog.GameEventsCog(bot)

    with patch("cogs.game_events.save_event", AsyncMock()):
        await cog._process_event(evt, now)

    guild.create_voice_channel.assert_awaited_once()
    assert evt.voice_channel_id == vc.id
    assert evt.state == "waiting"
    member_yes.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_channel_created_without_rsvp(monkeypatch):
    EVENTS.clear()
    now = datetime.now(timezone.utc)
    evt = GameEvent(
        id="e2",
        guild_id=1,
        creator_id=42,
        game_type="FPS",
        game_name="Apex",
        time=now + timedelta(minutes=5),
        channel_id=123,
        message_id=456,
    )
    evt.rsvps = {}

    member_creator = SimpleNamespace(id=42, display_name="Alice", send=AsyncMock())
    members = {42: member_creator}
    vc = SimpleNamespace(id=999, members=[], mention="<#999>")
    guild = SimpleNamespace(
        get_member=lambda uid: members.get(uid),
        create_voice_channel=AsyncMock(return_value=vc),
        get_channel=lambda cid: None,
    )
    bot = SimpleNamespace(get_guild=lambda gid: guild, add_view=lambda *a, **k: None)

    with patch.object(game_cog.tasks.Loop, "start", lambda self, *a, **k: None):
        with patch("cogs.game_events.load_events", lambda: None):
            cog = game_cog.GameEventsCog(bot)

    with patch("cogs.game_events.save_event", AsyncMock()):
        await cog._process_event(evt, now)

    guild.create_voice_channel.assert_awaited_once()
    assert evt.voice_channel_id == vc.id
    assert evt.state == "waiting"


@pytest.mark.asyncio
async def test_voice_channel_creation_http_exception(monkeypatch):
    EVENTS.clear()
    now = datetime.now(timezone.utc)
    evt = GameEvent(
        id="e3",
        guild_id=1,
        creator_id=42,
        game_type="FPS",
        game_name="Apex",
        time=now + timedelta(minutes=5),
        channel_id=123,
        message_id=456,
    )
    evt.rsvps = {"1": "yes"}
    evt.reminder_sent = True

    member_creator = SimpleNamespace(id=42, display_name="Alice", send=AsyncMock())
    members = {42: member_creator, 1: SimpleNamespace(id=1, send=AsyncMock())}
    http_exc = discord.HTTPException(SimpleNamespace(status=500, reason="fail"), "fail")
    guild = SimpleNamespace(
        get_member=lambda uid: members.get(uid),
        create_voice_channel=AsyncMock(side_effect=http_exc),
        get_channel=lambda cid: None,
    )
    bot = SimpleNamespace(get_guild=lambda gid: guild, add_view=lambda *a, **k: None)

    with patch.object(game_cog.tasks.Loop, "start", lambda self, *a, **k: None):
        with patch("cogs.game_events.load_events", lambda: None):
            cog = game_cog.GameEventsCog(bot)

    save_mock = AsyncMock()
    with patch("cogs.game_events.save_event", save_mock):
        await cog._process_event(evt, now)

    guild.create_voice_channel.assert_awaited_once()
    assert evt.voice_channel_id is None
    assert evt.state == "scheduled"
    save_mock.assert_not_awaited()
