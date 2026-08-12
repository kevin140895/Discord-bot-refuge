import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot


FORCED_STARTUP_COGS = (
    "economy_ui",
    "machine_a_sous",
    "temp_vc",
    "streamer_temp_vc",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_name", FORCED_STARTUP_COGS)
async def test_setup_hook_never_reloads_explicitly_disabled_fallback_cog(
    monkeypatch, disabled_name
):
    test_bot = bot.RefugeBot(command_prefix="!", intents=discord.Intents.none())

    monkeypatch.setattr(bot, "DISABLED_COGS", frozenset({disabled_name}))
    monkeypatch.setattr(bot, "configure_ytdlp_auth", MagicMock())
    monkeypatch.setattr(bot.xp_store, "start", AsyncMock())
    monkeypatch.setattr(bot.rename_manager, "start", AsyncMock())
    monkeypatch.setattr(bot.api_meter, "start", AsyncMock())
    monkeypatch.setattr(bot.limiter, "start", MagicMock())
    monkeypatch.setattr(bot, "reset_http_error_counter", AsyncMock())
    monkeypatch.setattr(bot.level_feed, "setup", MagicMock())
    monkeypatch.setattr(test_bot, "loop", asyncio.get_event_loop(), raising=False)

    # Reproduce the exact failure mode: discovery sees the cog and skips it
    # because it is disabled. The startup fallback must not load it afterwards.
    monkeypatch.setattr(
        bot.pkgutil,
        "iter_modules",
        lambda _path: [SimpleNamespace(name=disabled_name)],
    )

    load_extension = AsyncMock()
    monkeypatch.setattr(test_bot, "load_extension", load_extension)
    monkeypatch.setattr(test_bot.tree, "sync", AsyncMock())
    monkeypatch.setattr(test_bot, "add_view", MagicMock())

    await test_bot.setup_hook()

    loaded = [call.args[0] for call in load_extension.await_args_list]
    assert f"cogs.{disabled_name}" not in loaded

    for enabled_name in FORCED_STARTUP_COGS:
        if enabled_name != disabled_name:
            assert f"cogs.{enabled_name}" in loaded
