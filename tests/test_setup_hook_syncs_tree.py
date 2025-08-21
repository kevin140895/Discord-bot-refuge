import os
import sys
from pathlib import Path

import pytest
import discord
import asyncio
from unittest.mock import AsyncMock, MagicMock

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

import bot


@pytest.mark.asyncio
async def test_setup_hook_syncs_tree(monkeypatch):
    intents = discord.Intents.none()
    test_bot = bot.RefugeBot(command_prefix="!", intents=intents)

    monkeypatch.setattr(bot.xp_store, "start", AsyncMock())
    monkeypatch.setattr(bot.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(bot.channel_edit_manager, "start", AsyncMock())
    monkeypatch.setattr(bot.api_meter, "start", AsyncMock())
    monkeypatch.setattr(bot.limiter, "start", MagicMock())
    monkeypatch.setattr(bot, "reset_http_error_counter", AsyncMock())
    monkeypatch.setattr(test_bot, "loop", asyncio.get_event_loop(), raising=False)

    load_mock = AsyncMock()
    monkeypatch.setattr(test_bot, "load_extension", load_mock)

    sync_mock = AsyncMock()
    monkeypatch.setattr(test_bot.tree, "sync", sync_mock)

    await test_bot.setup_hook()

    sync_mock.assert_awaited_once()
