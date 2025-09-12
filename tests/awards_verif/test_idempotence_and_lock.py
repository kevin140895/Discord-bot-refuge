import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import discord

from cogs.daily_awards import DailyAwards, today_str_eu_paris


@pytest.mark.asyncio
async def test_idempotence_with_lock():
    data = {"top3": {"mvp": [{"id": 1, "score": 1.0, "messages": 1, "voice": 1}]}}

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace()
    state = {}
    cog._read_state = lambda: state.copy()
    cog._write_state = lambda s: state.update(s)
    embed = discord.Embed()
    cog._build_embed = AsyncMock(return_value=embed)

    msg = SimpleNamespace(id=123, embeds=[embed], edit=AsyncMock())
    channel = SimpleNamespace(send=AsyncMock(return_value=msg), fetch_message=AsyncMock(return_value=msg))
    cog._get_announce_channel = AsyncMock(return_value=channel)

    await asyncio.gather(
        DailyAwards._maybe_award(cog, data),
        DailyAwards._maybe_award(cog, data),
    )

    channel.send.assert_awaited_once()
    assert state["last_posted_date"] == today_str_eu_paris()
    assert state["last_message_id"] == 123
