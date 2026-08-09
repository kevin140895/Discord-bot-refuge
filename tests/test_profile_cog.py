from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from cogs.profile import ProfileCog


def test_profile_cog_registers_profile_command_and_member_context_menu() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = ProfileCog(bot)

    commands_registered = cog.get_app_commands()
    context_menu = bot.tree.get_command(
        "Profil du Refuge",
        type=discord.AppCommandType.user,
    )

    assert [command.name for command in commands_registered] == ["profil"]
    assert commands_registered[0].description == "Affiche le profil Refuge d'un membre"
    assert context_menu is cog.profile_context_menu
    assert cog.profile_context_menu.type is discord.AppCommandType.user

    cog.cog_unload()


def test_profile_context_menu_is_removed_when_cog_unloads() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = ProfileCog(bot)

    assert bot.tree.get_command(
        "Profil du Refuge",
        type=discord.AppCommandType.user,
    ) is not None

    cog.cog_unload()

    assert bot.tree.get_command(
        "Profil du Refuge",
        type=discord.AppCommandType.user,
    ) is None


@pytest.mark.asyncio
async def test_profile_context_menu_reuses_shared_profile_sender() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = ProfileCog(bot)
    cog._send_profile = AsyncMock()  # type: ignore[method-assign]
    interaction = SimpleNamespace()
    member = SimpleNamespace(id=42)

    await cog.profile_context_menu_callback(interaction, member)  # type: ignore[arg-type]

    cog._send_profile.assert_awaited_once_with(interaction, member)
    cog.cog_unload()
