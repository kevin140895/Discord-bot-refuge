import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from datetime import datetime, timedelta

import cogs.xp as xp


@pytest.mark.asyncio
async def test_message_awards_8_xp(monkeypatch):
    monkeypatch.setattr(xp, "schedule_checkpoint", AsyncMock())
    award = AsyncMock(return_value=(0, 0, 0, 0))
    monkeypatch.setattr(xp, "award_xp", award)
    bot = SimpleNamespace(announce_level_up=AsyncMock())
    cog = xp.XPCog(bot)
    msg = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=1),
        guild=SimpleNamespace(id=123),
        channel=SimpleNamespace(id=456),
    )
    await cog.on_message(msg)
    award.assert_awaited_once()
    assert award.await_args.args[1] == 8


@pytest.mark.asyncio
async def test_voice_awards_3_xp_per_minute(monkeypatch):
    monkeypatch.setattr(xp, "schedule_checkpoint", AsyncMock())
    monkeypatch.setattr(xp, "get_multiplier", lambda *a, **k: 1.0)
    monkeypatch.setattr(xp, "record_participant", lambda *a, **k: None)
    monkeypatch.setattr(xp, "get_voice_multiplier", lambda m: m)
    award = AsyncMock(return_value=(0, 0, 0, 0))
    monkeypatch.setattr(xp, "award_xp", award)
    bot = SimpleNamespace(announce_level_up=AsyncMock())
    cog = xp.XPCog(bot)

    xp.voice_times.clear()

    fixed_now = datetime(2025, 1, 1, 12, 0, tzinfo=xp.PARIS_TZ)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now
    monkeypatch.setattr(xp, "datetime", FixedDatetime)

    member = SimpleNamespace(bot=False, id=42, guild=SimpleNamespace(id=1, get_channel=lambda _id: None))
    uid = str(member.id)
    xp.voice_times[uid] = fixed_now - timedelta(minutes=1)
    before = SimpleNamespace(channel=SimpleNamespace(id=5))
    after = SimpleNamespace(channel=None)

    await cog.on_voice_state_update(member, before, after)
    award.assert_awaited_once()
    assert award.await_args.args[1] == 3
