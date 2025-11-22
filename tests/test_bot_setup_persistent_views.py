from __future__ import annotations

from typing import List, Type

from unittest.mock import AsyncMock

import discord
import pytest

from bot import RefugeBot
from view import PlayerTypeView, RadioView


@pytest.mark.asyncio
async def test_setup_hook_registers_persistent_views(monkeypatch):
    intents = discord.Intents.none()
    bot = RefugeBot(command_prefix="!", intents=intents)

    added: List[Type[discord.ui.View]] = []
    monkeypatch.setattr(bot, "add_view", lambda view: added.append(type(view)))

    monkeypatch.setattr("pkgutil.iter_modules", lambda path=None: [])
    monkeypatch.setattr(bot, "load_extension", AsyncMock())

    monkeypatch.setattr("bot.xp_store.start", AsyncMock())
    monkeypatch.setattr("bot.rename_manager.start", AsyncMock())
    monkeypatch.setattr("bot.channel_edit_manager.start", AsyncMock())
    monkeypatch.setattr("bot.api_meter.start", AsyncMock())
    monkeypatch.setattr("bot.reset_http_error_counter", AsyncMock())
    monkeypatch.setattr("bot.level_feed.setup", lambda _bot: None)
    monkeypatch.setattr("bot.limiter.start", lambda: None)

    bot.tree.sync = AsyncMock()

    await bot.setup_hook()

    assert PlayerTypeView in added
    assert RadioView in added
