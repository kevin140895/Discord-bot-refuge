import asyncio
import logging
import time

import discord
from discord.ext import commands
from config import (
    CHANNEL_EDIT_DEBOUNCE_SECONDS,
    CHANNEL_EDIT_MIN_INTERVAL_SECONDS,
    CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS,
)


_CHANNEL_LOCKS: dict[int, asyncio.Lock] = {}
_LAST_EDIT: dict[int, float] = {}
_MIN_INTERVAL = CHANNEL_EDIT_MIN_INTERVAL_SECONDS
_DEBOUNCE = CHANNEL_EDIT_DEBOUNCE_SECONDS
_GLOBAL_LOCK = asyncio.Lock()
_LAST_GLOBAL_EDIT = 0.0
_GLOBAL_MIN_INTERVAL = CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS

async def ensure_channel_has_message(
    bot: commands.Bot,
    channel_id: int,
    content: str,
) -> None:
    """
    Ensure the text channel with ``channel_id`` contains at least one message.

    If the channel has no history, send ``content``. Useful to guarantee
    that a channel isn't empty when the bot starts.
    """
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException as exc:
            logging.warning(
                "Impossible de récupérer le salon %s: %s", channel_id, exc
            )
            return

    try:
        async for _ in channel.history(limit=1):
            return
        await channel.send(content)
    except discord.Forbidden:
        logging.warning("Permissions insuffisantes dans %s", channel_id)
    except discord.HTTPException as exc:
        logging.warning("Erreur HTTP lors de l'envoi: %s", exc)


async def safe_channel_edit(channel: discord.abc.GuildChannel, **kwargs) -> None:
    """Safely edit a channel with rate limit protections."""
    global _LAST_GLOBAL_EDIT
    lock = _CHANNEL_LOCKS.setdefault(channel.id, asyncio.Lock())
    async with lock:
        if all(getattr(channel, k, None) == v for k, v in kwargs.items()):
            logging.debug("[safe_channel_edit] no-op for %s", channel.id)
            return

        if _DEBOUNCE > 0:
            await asyncio.sleep(_DEBOUNCE)

        now = time.monotonic()
        last = _LAST_EDIT.get(channel.id, 0)
        wait = _MIN_INTERVAL - (now - last)
        if wait > 0:
            logging.debug(
                "[safe_channel_edit] waiting %.1fs before editing %s", wait, channel.id
            )
            await asyncio.sleep(wait)

        async with _GLOBAL_LOCK:
            now = time.monotonic()
            gwait = _GLOBAL_MIN_INTERVAL - (now - _LAST_GLOBAL_EDIT)
            if gwait > 0:
                logging.debug(
                    "[safe_channel_edit] global wait %.1fs before editing %s",
                    gwait,
                    channel.id,
                )
                await asyncio.sleep(gwait)

            try:
                logging.debug(
                    "[safe_channel_edit] editing channel %s with %s", channel.id, kwargs
                )
                await channel.edit(**kwargs)
            except discord.NotFound:
                logging.warning(
                    "[safe_channel_edit] channel %s not found", channel.id
                )
                return
            except discord.HTTPException as exc:
                if exc.status == 429 and getattr(exc, "retry_after", None):
                    logging.warning(
                        "[safe_channel_edit] rate limited on %s, retry in %.1fs",
                        channel.id,
                        exc.retry_after,
                    )
                    await asyncio.sleep(exc.retry_after)
                    try:
                        await channel.edit(**kwargs)
                    except discord.NotFound:
                        logging.warning(
                            "[safe_channel_edit] channel %s not found", channel.id
                        )
                        return
                    except discord.HTTPException as exc2:
                        logging.warning(
                            "[safe_channel_edit] second edit failed for %s: %s",
                            channel.id,
                            exc2,
                        )
                        return
                else:
                    logging.warning(
                        "[safe_channel_edit] edit failed for %s: %s",
                        channel.id,
                        exc,
                    )
                    return
            _LAST_EDIT[channel.id] = time.monotonic()
            _LAST_GLOBAL_EDIT = _LAST_EDIT[channel.id]
