import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import discord

from cogs.daily_awards import DailyAwards


@pytest.mark.asyncio
async def test_embed_and_message_format():
    data = {
        "top3": {
            "mvp": [
                {
                    "id": 1,
                    "score": 373.3333,
                    "messages": 29,
                    "voice": 344,
                }
            ],
            "msg": [{"id": 2, "count": 29}],
            "vc": [{"id": 3, "minutes": 344}],
        }
    }

    cog = DailyAwards.__new__(DailyAwards)
    embed = await DailyAwards._build_embed(cog, data)

    assert embed.title == "📢 Annonce des gagnants — classement de 00h00"
    assert embed.colour.value == 0xFF1801
    assert len(embed.fields) == 3

    # MVP field formatting
    assert embed.fields[0].name == "MVP"
    assert "<@1>" in embed.fields[0].value
    assert "373.33" in embed.fields[0].value
    assert "messages : 29" in embed.fields[0].value
    assert "vocal : 5h 44m" in embed.fields[0].value

    # Writer field
    assert embed.fields[1].name == "Écrivain"
    assert embed.fields[1].value == "<@2>\nMessages envoyés : 29"

    # Voice field
    assert embed.fields[2].name == "Voix"
    assert embed.fields[2].value == "<@3>\nTemps en vocal : 5h 44m"

    # Footer date format
    assert re.match(r"Date : \d{2}/\d{2}/\d{4}$", embed.footer.text)

    # Check message content and allowed mentions through _maybe_award
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=42)), fetch_message=AsyncMock())
    cog.bot = SimpleNamespace()
    cog._read_state = lambda: {}
    cog._write_state = lambda _: None
    cog._get_announce_channel = AsyncMock(return_value=channel)

    await DailyAwards._maybe_award(cog, data)

    channel.send.assert_awaited_once()
    args, kwargs = channel.send.await_args
    assert args[0] == "@everyone"
    assert isinstance(kwargs["allowed_mentions"], discord.AllowedMentions)
    assert kwargs["allowed_mentions"].everyone is True
