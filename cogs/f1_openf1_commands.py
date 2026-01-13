from discord.ext import commands


class F1OpenF1Commands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(F1OpenF1Commands(bot))
