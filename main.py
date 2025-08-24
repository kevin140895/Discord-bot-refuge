from __future__ import annotations

import os
import discord

from bot import RefugeBot


def main() -> None:
    intents = discord.Intents.all()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set")
    bot = RefugeBot(command_prefix="!", intents=intents)
    bot.run(token)


if __name__ == "__main__":
    main()
