import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot


@pytest.mark.asyncio
async def test_setup_hook_skips_non_extension_support_modules(monkeypatch):
    test_bot = bot.RefugeBot(command_prefix="!", intents=discord.Intents.none())

    monkeypatch.setattr(bot.xp_store, "start", AsyncMock())
    monkeypatch.setattr(bot.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(bot.api_meter, "start", AsyncMock())
    monkeypatch.setattr(bot.limiter, "start", MagicMock())
    monkeypatch.setattr(bot, "reset_http_error_counter", AsyncMock())
    monkeypatch.setattr(bot.level_feed, "setup", MagicMock())
    monkeypatch.setattr(test_bot, "loop", asyncio.get_event_loop(), raising=False)

    monkeypatch.setattr(
        bot.pkgutil,
        "iter_modules",
        lambda _path: [
            SimpleNamespace(name="maitre_du_jeu"),
            SimpleNamespace(name="maitre_du_jeu_ai"),
        ],
    )

    load_extension = AsyncMock()
    monkeypatch.setattr(test_bot, "load_extension", load_extension)
    monkeypatch.setattr(test_bot.tree, "sync", AsyncMock())
    monkeypatch.setattr(test_bot, "add_view", MagicMock())

    await test_bot.setup_hook()

    loaded = [call.args[0] for call in load_extension.await_args_list]
    assert "cogs.maitre_du_jeu" in loaded
    assert "cogs.maitre_du_jeu_ai" not in loaded
    assert bot.COG_SUPPORT_MODULES == frozenset({"maitre_du_jeu_ai"})
