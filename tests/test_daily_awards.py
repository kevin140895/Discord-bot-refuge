from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock

from cogs.daily_awards import DailyAwards


@pytest.mark.asyncio
async def test_build_message_partial():
    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace()
    cog._mention_or_name = AsyncMock(side_effect=lambda uid: f"u{uid}")
    data = {
        "top3": {
            "mvp": [{"id": 1, "score": 10, "messages": 5, "voice": 30}]
        }
    }

    message = await DailyAwards._build_message(cog, data)
    assert "MVP du Refuge" in message
    assert "Écrivain du Refuge" in message and "Aucun gagnant" in message
    assert "Voix du Refuge" in message and message.count("Aucun gagnant") >= 2


@pytest.mark.asyncio
async def test_maybe_award_partial_publishes():
    channel = SimpleNamespace(send=AsyncMock())

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace()
    cog._read_state = lambda: {}
    cog._write_state = lambda state: None
    cog._build_message = AsyncMock(return_value="msg")
    cog._get_announce_channel = AsyncMock(return_value=channel)

    data = {"date": "2024-01-01", "winners": {"mvp": 1, "msg": None, "vc": None}}

    await DailyAwards._maybe_award(cog, data)

    cog._build_message.assert_awaited_once_with(data)
    channel.send.assert_awaited_once_with("msg")


