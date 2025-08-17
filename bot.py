import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook() -> None:
    await bot.load_extension("cogs.role_reminder")
    await bot.load_extension("cogs.roulette")
    await bot.load_extension("cogs.xp")
    await bot.load_extension("cogs.temp_vc")
    await bot.load_extension("cogs.misc")


TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN manquant. Ajoute la variable dans Railway > Service > Variables"
    )

if __name__ == "__main__":
    bot.run(TOKEN)