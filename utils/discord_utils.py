"""Helper functions for Discord channels and rate-limited edits."""

import logging
import time
from typing import Dict, Tuple

import discord
from discord.ext import commands

from utils.channel_edit_manager import channel_edit_manager
from utils.rate_limit import limiter

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


async def safe_message_edit(message: discord.Message, **kwargs) -> discord.Message | None:
    """Safely edit a message.

    Skips edits when content and embeds are unchanged and throttles requests
    using the shared rate limiter.  Returns the original message when no update
    is performed.
    """
    same_content = "content" not in kwargs or kwargs["content"] == getattr(message, "content", None)

    current_embeds = list(getattr(message, "embeds", []))
    if not current_embeds and getattr(message, "embed", None) is not None:
        current_embeds = [message.embed]  # type: ignore[attr-defined]

    same_embed = True
    if "embed" in kwargs:
        embed = kwargs["embed"]
        if embed is None:
            same_embed = len(current_embeds) == 0
        else:
            same_embed = (
                len(current_embeds) == 1
                and current_embeds[0].to_dict() == embed.to_dict()
            )
    elif "embeds" in kwargs:
        new_embeds = kwargs.get("embeds") or []
        same_embed = len(current_embeds) == len(new_embeds) and all(
            m.to_dict() == n.to_dict() for m, n in zip(current_embeds, new_embeds)
        )

    if same_content and same_embed:
        return message

    channel_id = getattr(getattr(message, "channel", None), "id", 0)
    await limiter.acquire(bucket=f"channel:{channel_id}")
    return await message.edit(**kwargs)

