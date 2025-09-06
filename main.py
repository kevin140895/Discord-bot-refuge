from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import discord

from bot import RefugeBot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


class DiscordCriticalHandler(logging.Handler):
    def __init__(self, bot: discord.Client, channel_id: int) -> None:
        super().__init__(level=logging.CRITICAL)
        self.bot = bot
        self.channel_id = channel_id

    async def _send(self, message: str) -> None:
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            await channel.send(f"```{message}```")

    def emit(self, record: logging.LogRecord) -> None:
        asyncio.create_task(self._send(self.format(record)))


def main() -> None:
    intents = discord.Intents(
        guilds=True,
        members=True,
        messages=True,
        reactions=True,
        voice_states=True,
        message_content=True,
        presences=True,
    )
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set")
    bot = RefugeBot(command_prefix="!", intents=intents)

    channel_id: Optional[str] = os.getenv("CRITICAL_LOG_CHANNEL_ID")
    if channel_id:
        handler = DiscordCriticalHandler(bot, int(channel_id))
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)

    bot.run(token)


if __name__ == "__main__":
    main()
