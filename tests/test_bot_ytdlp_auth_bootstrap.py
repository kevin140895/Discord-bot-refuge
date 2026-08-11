from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot


@pytest.mark.asyncio
async def test_setup_hook_configures_ytdlp_before_loading_extensions(monkeypatch):
    test_bot = bot.RefugeBot(command_prefix="!", intents=discord.Intents.none())
    events: list[str] = []

    monkeypatch.setattr(bot.xp_store, "start", AsyncMock())
    monkeypatch.setattr(bot.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(bot.api_meter, "start", AsyncMock())
    monkeypatch.setattr(bot.limiter, "start", MagicMock())
    monkeypatch.setattr(bot, "reset_http_error_counter", AsyncMock())
    monkeypatch.setattr(bot.level_feed, "setup", MagicMock())
    monkeypatch.setattr(bot.pkgutil, "iter_modules", lambda _path: [])
    monkeypatch.setattr(
        bot,
        "configure_ytdlp_auth",
        lambda: events.append("configure_ytdlp_auth"),
    )

    async def load_extension(name: str) -> None:
        events.append(f"load:{name}")

    monkeypatch.setattr(test_bot, "load_extension", load_extension)
    monkeypatch.setattr(test_bot.tree, "sync", AsyncMock())
    monkeypatch.setattr(test_bot, "add_view", MagicMock())

    await test_bot.setup_hook()

    assert events[0] == "configure_ytdlp_auth"
    assert any(event.startswith("load:cogs.") for event in events[1:])
