import asyncio
import logging
import os
import time

import discord
from discord.ext import commands


_CHANNEL_LOCKS: dict[int, asyncio.Lock] = {}
_LAST_EDIT: dict[int, float] = {}
_MIN_INTERVAL = int(os.getenv("CHANNEL_EDIT_MIN_INTERVAL_SECONDS", "180"))
_DEBOUNCE = int(os.getenv("CHANNEL_EDIT_DEBOUNCE_SECONDS", "15"))

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
    lock = _CHANNEL_LOCKS.setdefault(channel.id, asyncio.Lock())
    async with lock:
        if all(getattr(channel, k, None) == v for k, v in kwargs.items()):
            logging.info("[safe_channel_edit] no-op for %s", channel.id)
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

        try:
            logging.info(
                "[safe_channel_edit] editing channel %s with %s", channel.id, kwargs
            )
            await channel.edit(**kwargs)
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
                except discord.HTTPException:
                    logging.exception(
                        "[safe_channel_edit] second edit failed for %s", channel.id
                    )
                    raise
            else:
                raise
        _LAST_EDIT[channel.id] = time.monotonic()
