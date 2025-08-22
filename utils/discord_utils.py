"""Helper functions for Discord channels and rate-limited edits."""

import logging
import time
from typing import Dict, Tuple

import discord
from discord.ext import commands

from utils.channel_edit_manager import channel_edit_manager

_CHANNEL_CACHE: Dict[int, Tuple[discord.abc.GuildChannel, float]] = {}
_CACHE_TTL = 300.0  # seconds


async def _fetch_channel_cached(bot: commands.Bot, channel_id: int) -> discord.abc.GuildChannel | None:
    cached = _CHANNEL_CACHE.get(channel_id)
    now = time.monotonic()
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]
    try:
        channel = await bot.fetch_channel(channel_id)
    except discord.HTTPException as exc:
        logging.warning("Impossible de récupérer le salon %s: %s", channel_id, exc)
        return None
    _CHANNEL_CACHE[channel_id] = (channel, now)
    return channel


async def ensure_channel_has_message(
    bot: commands.Bot, channel_id: int, content: str
) -> None:
    """Ensure the text channel with ``channel_id`` has at least one message."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        channel = await _fetch_channel_cached(bot, channel_id)
        if channel is None:
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
    """Schedule a channel edit respecting configured rate limits."""
    await channel_edit_manager.request(channel, **kwargs)

