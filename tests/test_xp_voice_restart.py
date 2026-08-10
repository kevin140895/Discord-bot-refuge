from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.xp as xp


@pytest.mark.asyncio
async def test_on_ready_resets_persisted_voice_session_to_restart_time(monkeypatch):
    fixed_now = datetime(2026, 8, 10, 21, 30, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(xp, "datetime", FixedDatetime)
    checkpoint = AsyncMock()
    monkeypatch.setattr(xp, "schedule_checkpoint", checkpoint)

    active_member = SimpleNamespace(bot=False, id=42)
    bot_member = SimpleNamespace(bot=True, id=77)
    guild = SimpleNamespace(
        voice_channels=[SimpleNamespace(members=[active_member, bot_member])]
    )
    cog = object.__new__(xp.XPCog)
    cog.bot = SimpleNamespace(guilds=[guild])

    xp.voice_times.clear()
    xp.voice_times["42"] = fixed_now - timedelta(hours=5)
    xp.voice_times["99"] = fixed_now - timedelta(hours=1)

    await xp.XPCog.on_ready(cog)

    assert xp.voice_times == {"42": fixed_now}
    checkpoint.assert_awaited_once_with(xp.save_voice_times_to_disk)
