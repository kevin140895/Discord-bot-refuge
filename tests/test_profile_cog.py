import discord
from discord.ext import commands

from cogs.profile import ProfileCog


def test_profile_cog_registers_profile_command() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = ProfileCog(bot)

    commands_registered = cog.get_app_commands()

    assert [command.name for command in commands_registered] == ["profil"]
    assert commands_registered[0].description == "Affiche le profil Refuge d'un membre"
