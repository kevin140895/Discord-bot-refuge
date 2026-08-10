import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import discord
from discord.ext import commands

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.welcome import WelcomeCog
from config import CHANNEL_ROLES, CHANNEL_WELCOME


@pytest.mark.asyncio
async def test_welcome_message_sent():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    channel = SimpleNamespace(id=CHANNEL_WELCOME, send=AsyncMock())
    guild = SimpleNamespace(get_channel=lambda cid: channel if cid == CHANNEL_WELCOME else None)
    member = SimpleNamespace(
        id=123,
        guild=guild,
        mention="@member",
        bot=False,
        display_avatar=SimpleNamespace(url="https://example.invalid/avatar.png"),
    )

    cog = WelcomeCog(bot)
    await cog.on_member_join(member)

    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert set(kwargs) == {"embed"}

    embed = kwargs["embed"]
    assert embed.title == "🎉 Bienvenue au Refuge !"
    assert embed.description == (
        "@member, installe-toi bien !\n"
        f"🕹️ Choisis ton rôle dans le salon <#{CHANNEL_ROLES}> pour accéder à toutes les sections.\n"
        "Ravi de t’avoir parmi nous ! 🎮"
    )
    assert embed.image.url == "https://example.invalid/avatar.png"

    await bot.close()
