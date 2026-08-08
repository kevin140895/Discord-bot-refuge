import os
import sys
from pathlib import Path

import pytest
import discord
from unittest.mock import AsyncMock

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

import bot


@pytest.mark.asyncio
async def test_bot_close_stops_background_helpers(monkeypatch):
    intents = discord.Intents.none()
    test_bot = bot.RefugeBot(command_prefix="!", intents=intents)

    limiter_aclose_mock = AsyncMock()
    monkeypatch.setattr(bot.limiter, "aclose", limiter_aclose_mock)

    meter_aclose_mock = AsyncMock()
    monkeypatch.setattr(bot.api_meter, "aclose", meter_aclose_mock)

    rm_aclose_mock = AsyncMock()
    monkeypatch.setattr(bot.rename_manager, "aclose", rm_aclose_mock)

    cem_aclose_mock = AsyncMock()
    monkeypatch.setattr(bot.channel_edit_manager, "aclose", cem_aclose_mock)

    store_aclose_mock = AsyncMock()
    monkeypatch.setattr(bot.xp_store, "aclose", store_aclose_mock)

    super_close_mock = AsyncMock()
    from discord.ext import commands as d_commands

    monkeypatch.setattr(d_commands.Bot, "close", super_close_mock)

    await test_bot.close()

    limiter_aclose_mock.assert_awaited_once()
    meter_aclose_mock.assert_awaited_once()
    rm_aclose_mock.assert_awaited_once()
    cem_aclose_mock.assert_awaited_once()
    store_aclose_mock.assert_awaited_once()
    super_close_mock.assert_awaited_once()
