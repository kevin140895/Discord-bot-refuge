from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import discord

from bot import RefugeBot
from config import CRITICAL_LOG_CHANNEL_ID
from utils.background_tasks import BackgroundTaskRegistry, background_tasks


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
    """Forward CRITICAL logs to Discord as a best-effort secondary alert."""

    def __init__(
        self,
        bot: discord.Client,
        channel_id: int,
        *,
        task_registry: BackgroundTaskRegistry | None = None,
    ) -> None:
        super().__init__(level=logging.CRITICAL)
        self.bot = bot
        self.channel_id = channel_id
        self._task_registry = task_registry or background_tasks

    def _get_running_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = getattr(self.bot, "loop", None)
        if not isinstance(loop, asyncio.AbstractEventLoop):
            return None
        if loop.is_closed() or not loop.is_running():
            return None
        return loop

    async def _send(self, message: str) -> None:
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.channel_id)

        send = getattr(channel, "send", None)
        if not callable(send):
            raise TypeError(
                f"configured CRITICAL_LOG_CHANNEL_ID={self.channel_id} "
                "is not messageable"
            )

        await send(f"```{message}```")

    def _schedule_send(self, message: str) -> None:
        loop = self._get_running_loop()
        if loop is None:
            return

        try:
            self._task_registry.create_task(
                self._send(message),
                name=f"discord-critical-alert:{self.channel_id}",
            )
        except Exception:
            # Keep the logging pipeline fail-safe, but never hide a scheduling
            # failure: Railway/stdout remains the primary observability path.
            logging.getLogger(__name__).exception(
                "failed to schedule Discord CRITICAL alert"
            )

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

    if CRITICAL_LOG_CHANNEL_ID:
        handler = DiscordCriticalHandler(bot, CRITICAL_LOG_CHANNEL_ID)
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
