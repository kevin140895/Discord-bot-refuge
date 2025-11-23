import os
import sys
from pathlib import Path

import asyncio
import discord
import pytest
from unittest.mock import AsyncMock, MagicMock, call

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

import bot
import view


@pytest.mark.asyncio
async def test_setup_hook_registers_player_type_view_once(monkeypatch):
    intents = discord.Intents.none()
    test_bot = bot.RefugeBot(command_prefix="!", intents=intents)

    # Patch background helpers to avoid side effects
    monkeypatch.setattr(bot.xp_store, "start", AsyncMock())
    monkeypatch.setattr(bot.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(bot.channel_edit_manager, "start", AsyncMock())
    monkeypatch.setattr(bot.api_meter, "start", AsyncMock())
    monkeypatch.setattr(bot.limiter, "start", MagicMock())
    monkeypatch.setattr(bot, "reset_http_error_counter", AsyncMock())
    monkeypatch.setattr(test_bot, "loop", asyncio.get_event_loop(), raising=False)

    monkeypatch.setattr(test_bot, "load_extension", AsyncMock())
    monkeypatch.setattr(test_bot.tree, "sync", AsyncMock())

    add_view_mock = MagicMock()
    monkeypatch.setattr(test_bot, "add_view", add_view_mock)

    # First call registers the views
    await test_bot.setup_hook()
    # Second call should be idempotent
    await test_bot.setup_hook()
    assert add_view_mock.call_count == 2
    call_types = [type(c.args[0]) for c in add_view_mock.call_args_list]
    assert call_types == [view.PlayerTypeView, view.RadioView]
    bot.api_meter.start.assert_has_awaits([call(test_bot), call(test_bot)])

    # Simulate a restart with a new instance
    other_bot = bot.RefugeBot(command_prefix="!", intents=intents)
    monkeypatch.setattr(other_bot, "loop", asyncio.get_event_loop(), raising=False)
    monkeypatch.setattr(other_bot, "load_extension", AsyncMock())
    monkeypatch.setattr(other_bot.tree, "sync", AsyncMock())

    add_view_mock2 = MagicMock()
    monkeypatch.setattr(other_bot, "add_view", add_view_mock2)

    await other_bot.setup_hook()
    call_types = [type(c.args[0]) for c in add_view_mock2.call_args_list]
    assert call_types == [view.PlayerTypeView, view.RadioView]
    bot.api_meter.start.assert_has_awaits([call(test_bot), call(test_bot), call(other_bot)])

