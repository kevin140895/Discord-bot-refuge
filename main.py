from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import discord

from bot import RefugeBot


_RAILWAY_LEVELS = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class RailwayJsonFormatter(logging.Formatter):
    """Serialize application logs as one-line JSON understood by Railway."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "level": _RAILWAY_LEVELS.get(
                record.levelno,
                "error" if record.levelno >= logging.ERROR else "info",
            ),
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    """Configure a single stdout handler for Railway structured logging."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RailwayJsonFormatter())
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True,
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
    configure_logging()

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

    # discord.py installs its own stderr handler by default in Client.run().
    # Our root logger is already configured above, so disabling it prevents
    # duplicate lines and keeps Railway severity driven by the JSON level field.
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
