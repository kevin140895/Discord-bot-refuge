import os
import sys
from pathlib import Path

import pytest
import discord
from unittest.mock import AsyncMock, MagicMock

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

import bot


@pytest.mark.asyncio
async def test_bot_close_stops_rename_manager(monkeypatch):
    intents = discord.Intents.none()
    test_bot = bot.RefugeBot(command_prefix="!", intents=intents)

    stop_mock = MagicMock()
    monkeypatch.setattr(bot.rename_manager, "stop", stop_mock)

    aclose_mock = AsyncMock()
    monkeypatch.setattr(bot.xp_store, "aclose", aclose_mock)

    super_close_mock = AsyncMock()
    from discord.ext import commands as d_commands

    monkeypatch.setattr(d_commands.Bot, "close", super_close_mock)

    await test_bot.close()

    stop_mock.assert_called_once()
    aclose_mock.assert_awaited_once()
    super_close_mock.assert_awaited_once()
