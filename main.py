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

    def _get_running_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = getattr(self.bot, "loop", None)
        if not isinstance(loop, asyncio.AbstractEventLoop):
            return None
        if loop.is_closed() or not loop.is_running():
            return None
        return loop

    async def _send(self, message: str) -> None:
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            await channel.send(f"```{message}```")

    @staticmethod
    def _consume_send_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            # A logging handler must never surface delivery failures back into
            # the application or create "Task exception was never retrieved".
            pass

    def _schedule_send(self, message: str) -> None:
        loop = self._get_running_loop()
        if loop is None:
            return
        task = loop.create_task(self._send(message))
        task.add_done_callback(self._consume_send_result)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            loop = self._get_running_loop()
            if loop is None:
                return
            message = self.format(record)
            loop.call_soon_threadsafe(self._schedule_send, message)
        except RuntimeError:
            # The Discord loop can stop between the state check and scheduling.
            return
        except Exception:
            self.handleError(record)


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
