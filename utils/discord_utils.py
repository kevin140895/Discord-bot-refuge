"""Helper functions for Discord channels and rate-limited edits."""

import logging
import discord
from discord.ext import commands

from utils.channel_edit_manager import channel_edit_manager


async def ensure_channel_has_message(
    bot: commands.Bot, channel_id: int, content: str
) -> None:
    """Ensure the text channel with ``channel_id`` has at least one message."""
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
    """Schedule a channel edit respecting configured rate limits."""
    await channel_edit_manager.request(channel, **kwargs)

